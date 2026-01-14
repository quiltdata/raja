from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from ..models import CedarPolicy
from .entities import parse_entity

# Regex patterns for parsing Cedar schema
_ENTITY_RE = re.compile(r"entity\s+(\w+)\s*(?:in\s*\[([^\]]+)\])?\s*(?:\{[^}]*\})?", re.MULTILINE)
_ACTION_RE = re.compile(
    r'action\s+"([^"]+)"\s+appliesTo\s*\{\s*principal:\s*\[([^\]]+)\]\s*,\s*resource:\s*\[([^\]]+)\]\s*\}',
    re.MULTILINE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)


@dataclass(frozen=True)
class CedarSchema:
    resource_types: set[str]
    actions: set[str]

    def validate_policy(self, policy: CedarPolicy) -> None:
        """Validate policy resource and action types against the schema."""
        resource_type, _ = parse_entity(policy.resource)
        _, action_id = parse_entity(policy.action)

        if resource_type not in self.resource_types:
            raise ValueError(f"unknown resource type: {resource_type}")
        if action_id not in self.actions:
            raise ValueError(f"unknown action: {action_id}")


def parse_cedar_schema_to_avp_json(schema_text: str, namespace: str = "Raja") -> str:
    """Parse Cedar schema text and convert to AVP-compatible JSON format.

    This function parses Cedar schema declarations and generates the JSON format
    expected by Amazon Verified Permissions (AVP). It handles:
    - Entity declarations: entity User;
    - Entity hierarchies: entity S3Object in [S3Bucket];
    - Action declarations with appliesTo constraints
    - Comments (// style)

    Args:
        schema_text: Cedar schema as text string
        namespace: AVP namespace for the schema (default: "Raja")

    Returns:
        JSON string in AVP format

    Raises:
        ValueError: If schema parsing fails or is invalid

    Example:
        >>> schema = '''
        ... entity User {}
        ... entity Document {}
        ...
        ... action "read" appliesTo {
        ...   principal: [User],
        ...   resource: [Document]
        ... }
        ... '''
        >>> json_str = parse_cedar_schema_to_avp_json(schema)
        >>> # Returns AVP-compatible JSON with entities and actions
    """
    # Remove comments
    cleaned = _COMMENT_RE.sub("", schema_text)

    # Parse entity declarations
    entity_types: dict[str, dict[str, Any]] = {}
    for match in _ENTITY_RE.finditer(cleaned):
        entity_name = match.group(1)
        parent_types_str = match.group(2)

        # Build memberOfTypes list from parent entity types
        member_of_types = []
        if parent_types_str:
            # Parse comma-separated parent types
            parent_types = [p.strip() for p in parent_types_str.split(",")]
            member_of_types = parent_types

        entity_types[entity_name] = {"memberOfTypes": member_of_types}

    if not entity_types:
        raise ValueError("schema must contain at least one entity declaration")

    # Parse action declarations
    actions: dict[str, dict[str, Any]] = {}
    for match in _ACTION_RE.finditer(cleaned):
        action_name = match.group(1)
        principal_types_str = match.group(2)
        resource_types_str = match.group(3)

        # Parse comma-separated types
        principal_types = [p.strip() for p in principal_types_str.split(",")]
        resource_types = [r.strip() for r in resource_types_str.split(",")]

        actions[action_name] = {
            "appliesTo": {"principalTypes": principal_types, "resourceTypes": resource_types}
        }

    if not actions:
        raise ValueError("schema must contain at least one action declaration")

    # Build AVP schema structure
    avp_schema = {namespace: {"entityTypes": entity_types, "actions": actions}}

    return json.dumps(avp_schema)


def load_cedar_schema_from_file(file_path: str, namespace: str = "Raja") -> str:
    """Load Cedar schema from file and convert to AVP JSON format.

    Convenience function that reads a Cedar schema file and converts it to
    AVP-compatible JSON format.

    Args:
        file_path: Path to Cedar schema file
        namespace: AVP namespace for the schema (default: "Raja")

    Returns:
        JSON string in AVP format

    Raises:
        FileNotFoundError: If schema file does not exist
        ValueError: If schema parsing fails or is invalid
    """
    with open(file_path) as f:
        schema_text = f.read()
    return parse_cedar_schema_to_avp_json(schema_text, namespace)
