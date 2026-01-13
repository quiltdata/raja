from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

from .models import Scope

_SCOPE_PATTERN = re.compile(r"^(?P<resource_type>[^:]+):(?P<resource_id>[^:]+):(?P<action>[^:]+)$")


def parse_scope(scope_str: str) -> Scope:
    match = _SCOPE_PATTERN.match(scope_str)
    if not match:
        raise ValueError("scope must match 'ResourceType:ResourceId:Action'")
    return Scope(**match.groupdict())


def format_scope(resource_type: str, resource_id: str, action: str) -> str:
    return f"{resource_type}:{resource_id}:{action}"


def _normalize_scopes(scopes: Iterable[Scope | str]) -> set[str]:
    normalized: set[str] = set()
    for scope in scopes:
        if isinstance(scope, Scope):
            normalized.add(format_scope(scope.resource_type, scope.resource_id, scope.action))
        else:
            normalized.add(format_scope(**parse_scope(scope).model_dump()))
    return normalized


def is_subset(requested: Scope, granted: Sequence[Scope | str]) -> bool:
    requested_key = format_scope(requested.resource_type, requested.resource_id, requested.action)
    granted_keys = _normalize_scopes(granted)
    return requested_key in granted_keys
