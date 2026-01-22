from __future__ import annotations

import fnmatch
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit


@dataclass(frozen=True)
class QuiltUri:
    storage: str
    registry: str
    package_name: str
    hash: str
    path: str | None = None

    def normalized(self) -> str:
        registry = self.registry.rstrip("/")
        base = f"quilt+{self.storage.lower()}://{registry}#package={self.package_name}@{self.hash}"
        if self.path:
            normalized_path = self.path.replace("\\", "/")
            return f"{base}&path={normalized_path}"
        return base


def _parse_package_value(value: str) -> tuple[str, str]:
    if "@" not in value:
        raise ValueError("package value must include an immutable hash")
    package_name, package_hash = value.rsplit("@", 1)
    if not package_name or not package_hash:
        raise ValueError("package value must include name and hash")
    return package_name, package_hash


def parse_quilt_uri(uri: str) -> QuiltUri:
    """Parse and validate a Quilt+ URI string."""
    if not uri or not isinstance(uri, str):
        raise ValueError("quilt uri must be a non-empty string")

    split = urlsplit(uri)
    scheme = split.scheme
    if not scheme or not scheme.lower().startswith("quilt+"):
        raise ValueError("quilt uri must start with quilt+ scheme")

    storage = scheme.split("+", 1)[1].lower()
    if not storage:
        raise ValueError("quilt uri storage type is required")

    registry = f"{split.netloc}{split.path}".rstrip("/")
    if not registry:
        raise ValueError("quilt uri registry is required")

    fragment = split.fragment
    if not fragment:
        raise ValueError("quilt uri fragment is required")

    params = parse_qs(fragment, keep_blank_values=True)
    package_values = params.get("package")
    if not package_values or not package_values[0]:
        raise ValueError("quilt uri package parameter is required")

    package_name, package_hash = _parse_package_value(package_values[0])

    path_values = params.get("path")
    path = None
    if path_values:
        path_value = path_values[0]
        if not path_value:
            raise ValueError("quilt uri path parameter must be non-empty")
        path = path_value

    return QuiltUri(
        storage=storage,
        registry=registry,
        package_name=package_name,
        hash=package_hash,
        path=path,
    )


def normalize_quilt_uri(uri: str) -> str:
    """Return a canonical Quilt+ URI with normalized scheme and path separators."""
    parsed = parse_quilt_uri(uri)
    return parsed.normalized()


def validate_quilt_uri(uri: str) -> str:
    """Validate and normalize a Quilt+ URI for authorization use."""
    return normalize_quilt_uri(uri)


def package_name_matches(pattern: str, package_name: str) -> bool:
    """Return True if the package name matches a wildcard pattern."""
    if not pattern or not package_name:
        return False
    return fnmatch.fnmatchcase(package_name, pattern)
