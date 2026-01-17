from __future__ import annotations

import re
from typing import Literal, cast

from ..models import CedarPolicy
from .entities import parse_entity

_EFFECT_RE = re.compile(r"^(permit|forbid)\s*\(", re.IGNORECASE)
_FIELD_RE = re.compile(r"\b(principal|action|resource)\s*==\s*([^,\)]+)", re.IGNORECASE)
_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)
_IN_SPLIT_RE = re.compile(r"\s+in\s+", re.IGNORECASE)


def parse_resource_clause(resource_str: str) -> tuple[str, str, str | None, str | None]:
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

    if parent_type is not None:
        if resource_type != "S3Object" or parent_type != "S3Bucket":
            raise ValueError("resource hierarchy must be S3Object in S3Bucket")
    elif resource_type == "S3Object":
        raise ValueError("S3Object policies must include a parent S3Bucket")

    return resource_type, resource_id, parent_type, parent_id


def parse_policy(policy_str: str) -> CedarPolicy:
    """Parse a simplified Cedar policy string into a CedarPolicy model."""
    cleaned = _COMMENT_RE.sub("", policy_str).strip().rstrip(";")
    effect_match = _EFFECT_RE.match(cleaned)
    if not effect_match:
        raise ValueError("policy must start with permit(...) or forbid(...)")

    effect = cast(Literal["permit", "forbid"], effect_match.group(1).lower())
    fields = {key.lower(): value.strip() for key, value in _FIELD_RE.findall(cleaned)}

    if not {"principal", "action", "resource"}.issubset(fields.keys()):
        raise ValueError("policy must include principal, action, and resource")

    resource_type, resource_id, parent_type, parent_id = parse_resource_clause(fields["resource"])

    return CedarPolicy(
        effect=effect,
        principal=fields["principal"],
        action=fields["action"],
        resource=fields["resource"],
        resource_type=resource_type,
        resource_id=resource_id,
        parent_type=parent_type,
        parent_id=parent_id,
    )
