from __future__ import annotations

import re

from .cedar.parser import parse_policy
from .models import CedarPolicy
from .scope import format_scope

_ENTITY_RE = re.compile(r"^(?P<type>.+)::\"(?P<id>[^\"]+)\"$")


def _parse_entity(entity_str: str) -> tuple[str, str]:
    match = _ENTITY_RE.match(entity_str.strip())
    if not match:
        raise ValueError('entity must be in the form Type::"id"')
    raw_type = match.group("type")
    return raw_type.split("::")[-1], match.group("id")


def _action_id(action_str: str) -> str:
    try:
        _, action_id = _parse_entity(action_str)
        return action_id
    except ValueError:
        return action_str.strip().strip('"')


def _principal_id(policy: CedarPolicy) -> str:
    _, principal_id = _parse_entity(policy.principal)
    return principal_id


def _resource_parts(policy: CedarPolicy) -> tuple[str, str]:
    resource_type, resource_id = _parse_entity(policy.resource)
    return resource_type, resource_id


def compile_policy(cedar_policy: str) -> dict[str, list[str]]:
    """Compile a Cedar policy statement into a principal-to-scopes mapping."""
    parsed = parse_policy(cedar_policy)
    if parsed.effect != "permit":
        return {}

    principal = _principal_id(parsed)
    resource_type, resource_id = _resource_parts(parsed)
    action = _action_id(parsed.action)
    scope = format_scope(resource_type, resource_id, action)
    return {principal: [scope]}


def compile_policies(policies: list[str]) -> dict[str, list[str]]:
    """Compile multiple policies into a merged principal-to-scopes mapping."""
    compiled: dict[str, list[str]] = {}
    for policy in policies:
        mapping = compile_policy(policy)
        for principal, scopes in mapping.items():
            if principal not in compiled:
                compiled[principal] = []
            for scope in scopes:
                if scope not in compiled[principal]:
                    compiled[principal].append(scope)
    return compiled
