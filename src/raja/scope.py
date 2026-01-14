from __future__ import annotations

import re
from collections.abc import Iterable, Sequence

import structlog
from pydantic import ValidationError

from .exceptions import ScopeParseError, ScopeValidationError
from .models import Scope

logger = structlog.get_logger(__name__)

_SCOPE_PATTERN = re.compile(r"^(?P<resource_type>[^:]+):(?P<resource_id>[^:]+):(?P<action>.+)$")


def parse_scope(scope_str: str) -> Scope:
    """Parse a scope string into a Scope model.

    Args:
        scope_str: Scope string in format "ResourceType:ResourceId:Action"

    Returns:
        Scope model with parsed components

    Raises:
        ScopeParseError: If the scope string doesn't match expected format
        ScopeValidationError: If the parsed scope fails validation
    """
    match = _SCOPE_PATTERN.match(scope_str)
    if not match:
        logger.warning("scope_parse_failed", scope=scope_str)
        raise ScopeParseError(
            f"scope must match 'ResourceType:ResourceId:Action', got: {scope_str}"
        )

    try:
        return Scope(**match.groupdict())
    except ValidationError as exc:
        logger.warning("scope_validation_failed", scope=scope_str, error=str(exc))
        raise ScopeValidationError(f"invalid scope data: {exc}") from exc
    except Exception as exc:
        logger.error("unexpected_scope_parse_error", scope=scope_str, error=str(exc), exc_info=True)
        raise ScopeParseError(f"unexpected error parsing scope: {exc}") from exc


def format_scope(resource_type: str, resource_id: str, action: str) -> str:
    """Format a scope string from its components."""
    return f"{resource_type}:{resource_id}:{action}"


def _normalize_scopes(scopes: Iterable[Scope | str]) -> set[str]:
    """Normalize a collection of scopes into a set of scope strings.

    Args:
        scopes: Iterable of Scope objects or scope strings

    Returns:
        Set of normalized scope strings

    Raises:
        ScopeParseError: If any scope string cannot be parsed
        ScopeValidationError: If any scope fails validation
    """
    normalized: set[str] = set()
    for scope in scopes:
        try:
            if isinstance(scope, Scope):
                normalized.add(format_scope(scope.resource_type, scope.resource_id, scope.action))
            else:
                normalized.add(format_scope(**parse_scope(scope).model_dump()))
        except (ScopeParseError, ScopeValidationError):
            # Re-raise our custom exceptions
            raise
        except Exception as exc:
            logger.error(
                "unexpected_normalize_error", scope=str(scope), error=str(exc), exc_info=True
            )
            raise ScopeValidationError(f"unexpected error normalizing scope: {exc}") from exc
    return normalized


def is_subset(requested: Scope, granted: Sequence[Scope | str]) -> bool:
    """Check whether a requested scope is present in the granted scopes."""
    requested_key = format_scope(requested.resource_type, requested.resource_id, requested.action)
    granted_keys = _normalize_scopes(granted)
    return requested_key in granted_keys
