from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import CedarPolicy
from .entities import parse_entity
from .parser import parse_resource_clause

# Regex patterns for parsing Cedar schema
_ENTITY_RE = re.compile(r"entity\s+(\w+)\s*(?:in\s*\[([^\]]+)\])?\s*(?:\{[^}]*\})?", re.MULTILINE)
_ACTION_RE = re.compile(
    r'action\s+"([^"]+)"\s+appliesTo\s*\{\s*principal:\s*\[([^\]]+)\]\s*,\s*resource:\s*\[([^\]]+)\]\s*\}',
    re.MULTILINE | re.DOTALL,
)
_COMMENT_RE = re.compile(r"//.*$", re.MULTILINE)


@dataclass(frozen=True)
class CedarSchema:
    """Cedar schema for policy validation.

    Contains entity types and actions that can be referenced in policies.
    Used for compile-time validation of policy correctness.
    """

    resource_types: set[str]
    actions: set[str]
    principal_types: set[str] | None = None
    action_constraints: dict[str, dict[str, list[str]]] | None = None

    def validate_policy(self, policy: CedarPolicy) -> None:
        """Validate policy resource and action types against the schema.

        Args:
            policy: CedarPolicy to validate

        Raises:
            ValueError: If policy references unknown entities or actions
        """
        if policy.resource_type:
            resource_type = policy.resource_type
        else:
            resource_type, _, _, _ = parse_resource_clause(policy.resource)
        _, action_id = parse_entity(policy.action)

        if resource_type not in self.resource_types:
            raise ValueError(f"unknown resource type: {resource_type}")
        if action_id not in self.actions:
            raise ValueError(f"unknown action: {action_id}")

        # Validate principal type if schema includes principal types
        if self.principal_types is not None:
            principal_type, _ = parse_entity(policy.principal)
            if principal_type not in self.principal_types:
                raise ValueError(f"unknown principal type: {principal_type}")

        # Validate action applies to resource type
        if self.action_constraints is not None:
            action_info = self.action_constraints.get(action_id)
            if action_info is not None:
                allowed_resources = action_info.get("resourceTypes", [])
                if allowed_resources and resource_type not in allowed_resources:
                    raise ValueError(
                        f"action {action_id} cannot be applied to resource type {resource_type}"
                    )


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


def _cedar_cli_available() -> bool:
    """Check if Cedar CLI or Rust toolchain is available."""
    return bool(shutil.which("cargo")) or bool(os.environ.get("CEDAR_VALIDATE_BIN"))


def _run_cedar_validate_schema(schema_path: str) -> dict[str, Any]:
    """Validate Cedar schema file using Cedar CLI.

    Args:
        schema_path: Path to Cedar schema file

    Returns:
        Validation result as dict

    Raises:
        RuntimeError: If Cedar tooling is unavailable
        ValueError: If schema is invalid
    """
    if not _cedar_cli_available():
        raise RuntimeError("Cedar CLI or Rust toolchain is not available")

    # Use cedar check-parse to validate schema syntax
    cedar_bin = os.environ.get("CEDAR_VALIDATE_BIN", "cedar")

    try:
        result = subprocess.run(
            [cedar_bin, "check-parse", "--schema", schema_path, "--error-format", "json"],
            capture_output=True,
            text=True,
            check=False,
        )

        if result.returncode != 0:
            # Cedar CLI outputs errors in stdout with --error-format json
            error_output = result.stdout or result.stderr
            try:
                error_data = json.loads(error_output)
                # Extract message from Cedar CLI JSON error format
                error_msg = error_data.get("message", error_output)
            except (json.JSONDecodeError, KeyError):
                error_msg = error_output

            raise ValueError(f"Cedar schema validation failed: {error_msg}")

        return {"valid": True, "output": result.stdout}

    except FileNotFoundError:
        raise RuntimeError(f"Cedar CLI not found: {cedar_bin}")


def load_cedar_schema(schema_path: str, validate: bool = True) -> CedarSchema:
    """Load and parse Cedar schema file.

    Args:
        schema_path: Path to Cedar schema file
        validate: If True, validate schema using Cedar CLI

    Returns:
        CedarSchema object

    Raises:
        FileNotFoundError: If schema file does not exist
        ValueError: If schema is invalid
    """
    if validate and _cedar_cli_available():
        _run_cedar_validate_schema(schema_path)

    with open(schema_path) as f:
        schema_text = f.read()

    # Remove comments
    cleaned = _COMMENT_RE.sub("", schema_text)

    # Parse entity declarations
    entity_types: set[str] = set()
    for match in _ENTITY_RE.finditer(cleaned):
        entity_name = match.group(1)
        entity_types.add(entity_name)

    # Parse action declarations
    action_names: set[str] = set()
    action_constraints: dict[str, dict[str, list[str]]] = {}
    principal_types: set[str] = set()

    for match in _ACTION_RE.finditer(cleaned):
        action_name = match.group(1)
        principal_types_str = match.group(2)
        resource_types_str = match.group(3)

        action_names.add(action_name)

        # Parse comma-separated types
        principals = [p.strip() for p in principal_types_str.split(",")]
        resources = [r.strip() for r in resource_types_str.split(",")]

        action_constraints[action_name] = {"principalTypes": principals, "resourceTypes": resources}
        principal_types.update(principals)

    if not entity_types:
        raise ValueError("schema must contain at least one entity declaration")

    if not action_names:
        raise ValueError("schema must contain at least one action declaration")

    return CedarSchema(
        resource_types=entity_types,
        actions=action_names,
        principal_types=principal_types,
        action_constraints=action_constraints,
    )


def validate_policy_against_schema(
    policy_str: str, schema_path: str, use_cedar_cli: bool = True
) -> None:
    """Validate a Cedar policy against a schema.

    Args:
        policy_str: Cedar policy text
        schema_path: Path to Cedar schema file
        use_cedar_cli: If True, use Cedar CLI for validation

    Raises:
        ValueError: If policy violates schema constraints
        RuntimeError: If Cedar CLI is unavailable and use_cedar_cli is True
    """
    import tempfile

    if use_cedar_cli and _cedar_cli_available():
        # Use Cedar CLI for validation
        cedar_bin = os.environ.get("CEDAR_VALIDATE_BIN", "cedar")

        # Write policy to temporary file
        with tempfile.NamedTemporaryFile(mode="w", suffix=".cedar", delete=False) as f:
            f.write(policy_str)
            policy_path = f.name

        try:
            result = subprocess.run(
                [
                    cedar_bin,
                    "validate",
                    "--schema",
                    schema_path,
                    "--policies",
                    policy_path,
                    "--error-format",
                    "json",
                ],
                capture_output=True,
                text=True,
                check=False,
            )

            if result.returncode != 0:
                # Cedar CLI outputs errors in stdout with --error-format json
                error_output = result.stdout or result.stderr
                try:
                    error_data = json.loads(error_output)
                    # Extract message from Cedar CLI JSON error format
                    error_msg = error_data.get("message", error_output)
                except (json.JSONDecodeError, KeyError):
                    error_msg = error_output

                raise ValueError(f"Cedar policy validation failed: {error_msg}")

        finally:
            # Clean up temporary file
            os.unlink(policy_path)

        return

    # Fallback to basic schema validation
    schema = load_cedar_schema(schema_path, validate=False)
    # Parse policy and validate basic constraints
    from ..models import CedarPolicy
    from .parser import parse_policy

    parsed = parse_policy(policy_str, schema_path=None)
    # Create minimal CedarPolicy for validation
    policy = CedarPolicy(
        id="temp",
        effect=parsed.effect,
        principal=parsed.principal,
        action=parsed.actions[0] if parsed.actions else "",
        resource=f"{parsed.resource_type}::{parsed.resource_id}",
        resource_type=parsed.resource_type,
    )
    schema.validate_policy(policy)
