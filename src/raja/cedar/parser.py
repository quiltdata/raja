from __future__ import annotations

import re
from typing import Literal, cast

from ..models import CedarPolicy
from .entities import parse_entity

_EFFECT_RE = re.compile(r"^(permit|forbid)\s*\(", re.IGNORECASE)
_PRINCIPAL_RE = re.compile(r"\bprincipal\s*==\s*([^,\)&]+)", re.IGNORECASE)
_ACTION_RE = re.compile(r"\baction\s*==\s*([^,\)&]+)", re.IGNORECASE)
_RESOURCE_EQ_RE = re.compile(r"\bresource\s*==\s*([^,\)&]+)", re.IGNORECASE)
_RESOURCE_IN_RE = re.compile(r"\bresource\s+in\s+([^,\)&}]+)", re.IGNORECASE)
_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)
_IN_SPLIT_RE = re.compile(r"\s+in\s+", re.IGNORECASE)


def parse_resource_clause(
    resource_str: str, parent_str: str | None = None
) -> tuple[str, str, str | None, str | None]:
    if parent_str is None:
        parts = _IN_SPLIT_RE.split(resource_str.strip())
        if len(parts) == 1:
            resource_type, resource_id = parse_entity(parts[0])
            parent_type = None
            parent_id = None
        elif len(parts) == 2:
            resource_type, resource_id = parse_entity(parts[0])
            parent_type, parent_id = parse_entity(parts[1])
        else:
            raise ValueError("resource clause must include at most one 'in'")
    else:
        resource_type, resource_id = parse_entity(resource_str.strip())
        parent_type, parent_id = parse_entity(parent_str.strip())

    if parent_type is not None:
        if resource_type != "S3Object" or parent_type != "S3Bucket":
            raise ValueError("resource hierarchy must be S3Object in S3Bucket")
    elif resource_type == "S3Object":
        raise ValueError("S3Object policies must include a parent S3Bucket")

    if resource_type == "S3Object" and ("{{" in resource_id or "}}" in resource_id):
        raise ValueError("template placeholders are only allowed in bucket identifiers")

    return resource_type, resource_id, parent_type, parent_id


def parse_policy(policy_str: str) -> CedarPolicy:
    """Parse a simplified Cedar policy string into a CedarPolicy model."""
    cleaned = _COMMENT_RE.sub("", policy_str).strip().rstrip(";")
    effect_match = _EFFECT_RE.match(cleaned)
    if not effect_match:
        raise ValueError("policy must start with permit(...) or forbid(...)")

    effect = cast(Literal["permit", "forbid"], effect_match.group(1).lower())

    principal_match = _PRINCIPAL_RE.search(cleaned)
    action_match = _ACTION_RE.search(cleaned)
    resource_match = _RESOURCE_EQ_RE.search(cleaned)
    parent_match = _RESOURCE_IN_RE.search(cleaned)

    if not principal_match or not action_match or not resource_match:
        raise ValueError("policy must include principal, action, and resource")

    principal = principal_match.group(1).strip()
    action = action_match.group(1).strip()
    resource = resource_match.group(1).strip()
    parent_clause = parent_match.group(1).strip() if parent_match else None

    resource_type, resource_id, parent_type, parent_id = parse_resource_clause(
        resource, parent_clause
    )

    return CedarPolicy(
        effect=effect,
        principal=principal,
        action=action,
        resource=resource,
        resource_type=resource_type,
        resource_id=resource_id,
        parent_type=parent_type,
        parent_id=parent_id,
    )
