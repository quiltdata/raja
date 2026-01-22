from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .entities import parse_entity


@dataclass(frozen=True)
class ParsedPolicy:
    effect: Literal["permit", "forbid"]
    principal: str
    actions: list[str]
    resource_type: str
    resource_id: str
    parent_type: str | None
    parent_ids: list[str]


def parse_resource_clause(
    resource_str: str, parent_str: str | None = None
) -> tuple[str, str, str | None, str | None]:
    """Parse resource clause from policy string.

    Extracts resource type, ID, and optional parent relationship.
    Used for backward compatibility with non-Cedar validation code.
    """
    resource_type, resource_id = parse_entity(resource_str.strip())
    parent_type: str | None = None
    parent_id: str | None = None

    if parent_str is not None:
        parent_type, parent_id = parse_entity(parent_str.strip())
    elif " in " in resource_str:
        resource_part, parent_part = resource_str.split(" in ", 1)
        resource_type, resource_id = parse_entity(resource_part.strip())
        parent_type, parent_id = parse_entity(parent_part.strip())

    if parent_type is not None:
        if resource_type != "S3Object" or parent_type != "S3Bucket":
            raise ValueError("resource hierarchy must be S3Object in S3Bucket")
    elif resource_type == "S3Object":
        raise ValueError("S3Object policies must include a parent S3Bucket")

    if resource_type == "S3Object" and ("{{" in resource_id or "}}" in resource_id):
        raise ValueError("template placeholders are only allowed in bucket identifiers")

    return resource_type, resource_id, parent_type, parent_id


def _format_entity(entity: dict[str, Any]) -> str:
    """Format entity dict to Cedar entity string: Type::"id"."""
    entity_type = entity.get("type")
    entity_id = entity.get("id")
    if not isinstance(entity_type, str) or not isinstance(entity_id, str):
        raise ValueError("invalid entity format in Cedar policy")
    return f'{entity_type}::"{entity_id}"'


def _extract_entity_id(entity: dict[str, Any]) -> str:
    """Extract entity ID from entity dict."""
    entity_id = entity.get("id")
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("invalid entity id in Cedar policy")
    return entity_id


def _collect_resource_in(expr: dict[str, Any]) -> list[str]:
    """Collect resource parent IDs from 'resource in' conditions.

    Handles:
    - Single condition: resource in S3Bucket::"bucket-a"
    - OR conditions: resource in S3Bucket::"a" || resource in S3Bucket::"b"
    """
    if "in" in expr:
        clause = expr["in"]
        left = clause.get("left", {})
        right = clause.get("right", {})
        if left.get("Var") != "resource":
            raise ValueError("unsupported condition: resource in must target resource")
        value = right.get("Value", {})
        entity = value.get("__entity")
        if not isinstance(entity, dict):
            raise ValueError("unsupported condition: resource in must target entity")
        entity_type = entity.get("type")
        if not isinstance(entity_type, str):
            raise ValueError("invalid entity type in condition")
        entity_type_short = entity_type.split("::")[-1]
        if entity_type_short != "S3Bucket":
            raise ValueError("resource hierarchy must be S3Object in S3Bucket")
        return [_extract_entity_id(entity)]

    if "||" in expr:
        left = expr["||"].get("left", {})
        right = expr["||"].get("right", {})
        return _collect_resource_in(left) + _collect_resource_in(right)

    raise ValueError("unsupported policy condition")


def _parse_conditions(conditions: list[dict[str, Any]]) -> tuple[str | None, list[str]]:
    """Parse Cedar policy conditions (when/unless clauses).

    Currently supports:
    - when { resource in S3Bucket::"bucket" }
    - OR combinations of resource in conditions

    Rejects:
    - unless clauses
    - Complex conditions (context, AND combinations, etc.)
    """
    parent_ids: list[str] = []
    for condition in conditions:
        if condition.get("kind") != "when":
            raise ValueError("only when clauses are supported")
        body = condition.get("body")
        if not isinstance(body, dict):
            raise ValueError("invalid condition body")
        parent_ids.extend(_collect_resource_in(body))

    if not parent_ids:
        return None, []
    return "S3Bucket", parent_ids


def _run_cedar_parse(policy_str: str, schema_path: str | None = None) -> dict[str, Any]:
    """Run Cedar parser via Rust subprocess.

    Uses either:
    - CEDAR_PARSE_BIN environment variable (pre-built binary)
    - Cargo to run cedar_parse tool from tools/cedar-validate

    Args:
        policy_str: Cedar policy text to parse
        schema_path: Optional path to Cedar schema for validation

    Returns:
        Parsed policy as dict

    Raises:
        RuntimeError: If Cedar tooling is unavailable
        ValueError: If policy is invalid
    """
    cedar_bin = os.environ.get("CEDAR_PARSE_BIN")
    if cedar_bin:
        command = [cedar_bin]
        if schema_path:
            command.extend(["--schema", schema_path])
        workdir = None
    else:
        if not shutil.which("cargo"):
            raise RuntimeError("cargo is required to parse Cedar policies")
        repo_root = Path(__file__).resolve().parents[3]
        command = ["cargo", "run", "--quiet", "--bin", "cedar_parse"]
        if schema_path:
            command.extend(["--", "--schema", schema_path])
        workdir = str(repo_root / "tools" / "cedar-validate")

    result = subprocess.run(
        command,
        input=policy_str,
        text=True,
        capture_output=True,
        cwd=workdir,
        check=False,
    )
    if result.returncode != 0:
        error_msg = result.stderr.strip() or "failed to parse Cedar policy"
        raise ValueError(f"Cedar policy validation failed: {error_msg}")

    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Cedar parser output: {exc}") from exc

    if not isinstance(parsed, dict):
        raise ValueError("invalid Cedar parser output")

    return parsed


def parse_policy(policy_str: str, schema_path: str | None = None) -> ParsedPolicy:
    """Parse a Cedar policy statement.

    Args:
        policy_str: Cedar policy text
        schema_path: Optional path to Cedar schema for validation

    Returns:
        ParsedPolicy with extracted components

    Raises:
        ValueError: If policy is invalid or malformed
        RuntimeError: If Cedar parsing tooling is unavailable
    """
    parsed = _run_cedar_parse(policy_str, schema_path)

    # Extract components from Cedar CLI output
    effect = parsed.get("effect")
    if effect not in {"permit", "forbid"}:
        raise ValueError("policy must include a permit/forbid effect")

    principal = parsed.get("principal", {})
    principal_op = principal.get("op")
    principal_entity = principal.get("entity")
    if principal_op not in {"==", "in"} or not isinstance(principal_entity, dict):
        raise ValueError("policy must include a concrete principal")
    principal_str = _format_entity(principal_entity)

    action = parsed.get("action", {})
    action_op = action.get("op")
    actions: list[str] = []
    if action_op == "==":
        entity = action.get("entity")
        if not isinstance(entity, dict):
            raise ValueError("policy must include a concrete action")
        actions.append(_extract_entity_id(entity))
    elif action_op == "in":
        entities = action.get("entities")
        if not isinstance(entities, list) or not entities:
            raise ValueError("policy must include at least one action")
        for entity in entities:
            if not isinstance(entity, dict):
                raise ValueError("invalid action entity in Cedar policy")
            actions.append(_extract_entity_id(entity))
    else:
        raise ValueError("policy must include an action constraint")

    resource = parsed.get("resource", {})
    resource_op = resource.get("op")
    resource_entity = resource.get("entity")
    if resource_op != "==" or not isinstance(resource_entity, dict):
        raise ValueError("policy must include a concrete resource")
    resource_type, resource_id = parse_entity(_format_entity(resource_entity))

    conditions = parsed.get("conditions", [])
    if not isinstance(conditions, list):
        raise ValueError("invalid policy conditions")
    parent_type, parent_ids = _parse_conditions(conditions)

    return ParsedPolicy(
        effect=effect,
        principal=principal_str,
        actions=actions,
        resource_type=resource_type,
        resource_id=resource_id,
        parent_type=parent_type,
        parent_ids=parent_ids,
    )
