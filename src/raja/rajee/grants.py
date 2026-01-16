from __future__ import annotations

from collections.abc import Iterable


def convert_scope_to_grant(scope: str) -> str | None:
    """Convert a compiled S3 scope into a RAJEE grant string."""
    if scope.startswith("s3:") and "/" in scope:
        return scope

    parts = scope.split(":", 2)
    if len(parts) != 3:
        return None

    resource_type, resource_id, action = parts

    if resource_type == "S3Object":
        return f"{action}/{resource_id}"
    if resource_type == "S3Bucket":
        bucket = resource_id.rstrip("/")
        return f"{action}/{bucket}/"

    return None


def convert_scopes_to_grants(scopes: Iterable[str]) -> list[str]:
    """Convert a list of compiled scopes into RAJEE grant strings."""
    grants: list[str] = []
    for scope in scopes:
        grant = convert_scope_to_grant(scope)
        if grant:
            grants.append(grant)
    return grants
