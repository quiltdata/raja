from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

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


_LEGACY_EFFECT_RE = re.compile(r"^(permit|forbid)\s*\(", re.IGNORECASE)
_LEGACY_PRINCIPAL_RE = re.compile(r"\bprincipal\s*(==|in)\s*([^,\)&]+)", re.IGNORECASE)
_LEGACY_ACTION_RE = re.compile(r"\baction\s*(==|in)\s*([^,\)&]+)", re.IGNORECASE)
_LEGACY_ACTION_LIST_RE = re.compile(r"\baction\s+in\s*\[([^\]]+)\]", re.IGNORECASE)
_LEGACY_RESOURCE_RE = re.compile(r"\bresource\s*==\s*([^,\)&]+)", re.IGNORECASE)
_LEGACY_RESOURCE_IN_RE = re.compile(r"\bresource\s+in\s+([^,\)&}]+)", re.IGNORECASE)


def _legacy_parse_policy(policy_str: str) -> ParsedPolicy:
    cleaned = re.sub(r"//.*$", "", policy_str, flags=re.MULTILINE).strip().rstrip(";")
    effect_match = _LEGACY_EFFECT_RE.match(cleaned)
    if not effect_match:
        raise ValueError("policy must start with permit(...) or forbid(...)")
    effect = cast(Literal["permit", "forbid"], effect_match.group(1).lower())

    principal_match = _LEGACY_PRINCIPAL_RE.search(cleaned)
    action_match = _LEGACY_ACTION_RE.search(cleaned)
    resource_match = _LEGACY_RESOURCE_RE.search(cleaned)
    if not principal_match or not action_match or not resource_match:
        raise ValueError("policy must include principal, action, and resource")

    principal = principal_match.group(2).strip()
    action_clause = action_match.group(2).strip()
    resource = resource_match.group(1).strip()

    actions: list[str] = []
    list_match = _LEGACY_ACTION_LIST_RE.search(cleaned)
    if list_match:
        for raw in list_match.group(1).split(","):
            _, action_id = parse_entity(raw.strip())
            actions.append(action_id)
    else:
        _, action_id = parse_entity(action_clause)
        actions.append(action_id)

    resource_type, resource_id = parse_entity(resource)

    parent_ids: list[str] = []
    parent_type: str | None = None
    for match in _LEGACY_RESOURCE_IN_RE.finditer(cleaned):
        parent_entity = match.group(1).strip()
        parent_type_value, parent_id_value = parse_entity(parent_entity)
        if parent_type_value != "S3Bucket":
            raise ValueError("resource hierarchy must be S3Object in S3Bucket")
        parent_type = parent_type_value
        parent_ids.append(parent_id_value)

    return ParsedPolicy(
        effect=effect,
        principal=principal,
        actions=actions,
        resource_type=resource_type,
        resource_id=resource_id,
        parent_type=parent_type,
        parent_ids=parent_ids,
    )


def parse_resource_clause(
    resource_str: str, parent_str: str | None = None
) -> tuple[str, str, str | None, str | None]:
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
    entity_type = entity.get("type")
    entity_id = entity.get("id")
    if not isinstance(entity_type, str) or not isinstance(entity_id, str):
        raise ValueError("invalid entity format in Cedar policy")
    return f'{entity_type}::"{entity_id}"'


def _extract_entity_id(entity: dict[str, Any]) -> str:
    entity_id = entity.get("id")
    if not isinstance(entity_id, str) or not entity_id:
        raise ValueError("invalid entity id in Cedar policy")
    return entity_id


def _collect_resource_in(expr: dict[str, Any]) -> list[str]:
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


def _run_cedar_parse(policy_str: str) -> dict[str, Any]:
    cedar_bin = os.environ.get("CEDAR_PARSE_BIN")
    if cedar_bin:
        command = [cedar_bin]
        workdir = None
    else:
        if not shutil.which("cargo"):
            raise RuntimeError("cargo is required to parse Cedar policies")
        repo_root = Path(__file__).resolve().parents[3]
        command = ["cargo", "run", "--quiet", "--bin", "cedar_parse"]
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
        raise ValueError(result.stderr.strip() or "failed to parse Cedar policy")
    try:
        parsed = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid Cedar parser output: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("invalid Cedar parser output")
    return parsed


def parse_policy(policy_str: str) -> ParsedPolicy:
    """Parse a Cedar policy statement using the Cedar Rust parser."""
    try:
        parsed = _run_cedar_parse(policy_str)
    except RuntimeError as exc:
        warnings.warn(
            f"falling back to legacy Cedar parsing: {exc}",
            RuntimeWarning,
            stacklevel=2,
        )
        return _legacy_parse_policy(policy_str)

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
