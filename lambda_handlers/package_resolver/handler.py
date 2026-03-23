from __future__ import annotations

from raja.manifest import package_membership_checker, resolve_package_manifest, resolve_package_map
from raja.models import S3Location
from raja.package_map import PackageMap

__all__ = ["resolve_manifest", "resolve_translation_map", "check_membership"]


def resolve_manifest(quilt_uri: str) -> list[S3Location]:
    """Resolve a Quilt+ URI to a list of physical locations."""
    return resolve_package_manifest(quilt_uri)


def resolve_translation_map(quilt_uri: str) -> PackageMap:
    """Resolve a Quilt+ URI to a logical-to-physical map."""
    return resolve_package_map(quilt_uri)


def check_membership(quilt_uri: str, bucket: str, key: str) -> bool:
    """Return True if the bucket/key is a member of the Quilt package."""
    return package_membership_checker(quilt_uri, bucket, key)
