from __future__ import annotations

from .cedar.entities import parse_entity
from .cedar.parser import parse_policy
from .models import CedarPolicy
from .scope import format_scope


def _action_id(action_str: str) -> str:
    try:
        _, action_id = parse_entity(action_str)
        return action_id
    except ValueError:
        return action_str.strip().strip('"')


def _principal_id(policy: CedarPolicy) -> str:
    _, principal_id = parse_entity(policy.principal)
    return principal_id


def _resource_parts(policy: CedarPolicy) -> tuple[str, str]:
    resource_type, resource_id = parse_entity(policy.resource)
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
