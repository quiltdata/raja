from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import CedarPolicy

_ENTITY_RE = re.compile(r"^(?P<type>.+)::\"(?P<id>[^\"]+)\"$")


def _parse_entity(entity_str: str) -> tuple[str, str]:
    match = _ENTITY_RE.match(entity_str.strip())
    if not match:
        raise ValueError('entity must be in the form Type::"id"')
    raw_type = match.group("type")
    return raw_type.split("::")[-1], match.group("id")


@dataclass(frozen=True)
class CedarSchema:
    resource_types: set[str]
    actions: set[str]

    def validate_policy(self, policy: CedarPolicy) -> None:
        """Validate policy resource and action types against the schema."""
        resource_type, _ = _parse_entity(policy.resource)
        _, action_id = _parse_entity(policy.action)

        if resource_type not in self.resource_types:
            raise ValueError(f"unknown resource type: {resource_type}")
        if action_id not in self.actions:
            raise ValueError(f"unknown action: {action_id}")
