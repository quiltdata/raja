from __future__ import annotations

from collections.abc import Iterable

from .models import S3Location
from .package_map import PackageMap
from .quilt_uri import parse_quilt_uri


def _load_quilt3():
    try:
        import quilt3  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - exercised via callers
        raise RuntimeError("quilt3 is required for package resolution") from exc
    return quilt3


def _iter_locations(entries: Iterable[tuple[str, object]]) -> list[tuple[str, S3Location]]:
    locations: list[tuple[str, S3Location]] = []
    for logical_path, entry in entries:
        bucket = getattr(entry, "bucket", None)
        key = getattr(entry, "key", None)
        if not bucket or not key:
            continue
        locations.append((logical_path, S3Location(bucket=bucket, key=key)))
    return locations


def resolve_package_manifest(quilt_uri: str) -> list[S3Location]:
    """Resolve a Quilt+ URI to a list of physical S3 locations."""
    parsed = parse_quilt_uri(quilt_uri)
    quilt3 = _load_quilt3()
    package = quilt3.Package.browse(
        name=parsed.package_name,
        registry=f"{parsed.storage}://{parsed.registry}",
        top_hash=parsed.hash,
    )
    locations = _iter_locations(package.walk())
    return [location for _, location in locations]


def resolve_package_map(quilt_uri: str) -> PackageMap:
    """Resolve a Quilt+ URI to a logical-to-physical package map."""
    parsed = parse_quilt_uri(quilt_uri)
    quilt3 = _load_quilt3()
    package = quilt3.Package.browse(
        name=parsed.package_name,
        registry=f"{parsed.storage}://{parsed.registry}",
        top_hash=parsed.hash,
    )
    mapping: dict[str, list[S3Location]] = {}
    for logical_path, location in _iter_locations(package.walk()):
        mapping.setdefault(logical_path, []).append(location)
    return PackageMap(entries=mapping)


def package_membership_checker(quilt_uri: str, bucket: str, key: str) -> bool:
    """Return True if the bucket/key is a member of the Quilt package."""
    for location in resolve_package_manifest(quilt_uri):
        if location.bucket == bucket and location.key == key:
            return True
    return False
