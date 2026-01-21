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

    action = match.group("action")
    if action.count(":") > 1:
        logger.warning("scope_parse_failed_extra_colons", scope=scope_str)
        raise ScopeParseError("scope contains invalid colons in resource_id or action")

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


def matches_pattern(value: str, pattern: str) -> bool:
    """Check if a value matches a pattern with wildcard support.

    Phase 4: Wildcard Pattern Matching

    Supports:
    - Exact match: "value" matches "value"
    - Wildcard match: "*" matches any value
    - Prefix match: "s3:*" matches "s3:GetObject", "s3:PutObject", etc.
    - Suffix match: "*:read" matches "doc:read", "file:read", etc.

    Args:
        value: String value to test
        pattern: Pattern with optional wildcards (*)

    Returns:
        True if value matches pattern, False otherwise

    Examples:
        >>> matches_pattern("s3:GetObject", "s3:*")
        True
        >>> matches_pattern("s3:GetObject", "s3:GetObject")
        True
        >>> matches_pattern("s3:GetObject", "dynamodb:*")
        False
    """
    if pattern == "*":
        return True

    if "*" not in pattern:
        return value == pattern

    # Convert wildcard pattern to regex
    regex_pattern = re.escape(pattern).replace(r"\*", ".*")
    return bool(re.fullmatch(regex_pattern, value))


def scope_matches(requested: Scope, granted: Scope) -> bool:
    """Check if a requested scope is covered by a granted scope.

    Phase 4: Enhanced Wildcard Matching

    Supports:
    - Exact matches: Document:doc123:read ⊆ Document:doc123:read
    - Resource wildcards: Document:doc123:* ⊆ Document:doc123:read
    - Resource type wildcards: *:doc123:read ⊆ Document:doc123:read
    - Action wildcards: Document:doc123:s3:* ⊆ Document:doc123:s3:GetObject
    - Full wildcards: *:*:* ⊆ anything

    Args:
        requested: Requested scope
        granted: Granted scope (may contain wildcards)

    Returns:
        True if granted scope covers requested scope, False otherwise
    """
    return (
        matches_pattern(requested.resource_type, granted.resource_type)
        and matches_pattern(requested.resource_id, granted.resource_id)
        and matches_pattern(requested.action, granted.action)
    )


def expand_wildcard_scope(
    scope_pattern: str, resource_types: list[str] | None = None, actions: list[str] | None = None
) -> list[str]:
    """Expand a wildcard scope pattern into concrete scopes.

    Phase 4: Wildcard Expansion

    Args:
        scope_pattern: Scope pattern (may contain wildcards)
        resource_types: List of valid resource types for expansion
        actions: List of valid actions for expansion

    Returns:
        List of concrete scope strings

    Raises:
        ValueError: If pattern cannot be expanded without context

    Examples:
        >>> expand_wildcard_scope("Document:*:read")
        ["Document:*:read"]  # Cannot expand without resource list

        >>> expand_wildcard_scope("*:doc123:read", resource_types=["Document", "File"])
        ["Document:doc123:read", "File:doc123:read"]
    """
    scope = parse_scope(scope_pattern)

    # If no wildcards, return as-is
    if "*" not in scope_pattern:
        return [scope_pattern]

    expanded: list[str] = []

    # Expand resource type wildcards
    if scope.resource_type == "*":
        if not resource_types:
            raise ValueError("cannot expand resource type wildcard without context")
        for resource_type in resource_types:
            expanded.extend(
                expand_wildcard_scope(
                    format_scope(resource_type, scope.resource_id, scope.action),
                    resource_types=None,
                    actions=actions,
                )
            )
        return expanded

    # Expand action wildcards
    if "*" in scope.action:
        if not actions:
            # Cannot expand action wildcards without action list
            # Return pattern as-is for runtime matching
            return [scope_pattern]
        matching_actions = [a for a in actions if matches_pattern(a, scope.action)]
        for action in matching_actions:
            expanded.append(format_scope(scope.resource_type, scope.resource_id, action))
        return expanded

    # Resource ID wildcards cannot be expanded (need runtime data)
    return [scope_pattern]


def filter_scopes_by_pattern(
    scopes: Sequence[str],
    include_patterns: list[str] | None = None,
    exclude_patterns: list[str] | None = None,
) -> list[str]:
    """Filter scopes by inclusion and exclusion patterns.

    Phase 4: Scope Filtering for Forbid Support

    Args:
        scopes: List of scope strings
        include_patterns: Patterns that scopes must match (None = include all)
        exclude_patterns: Patterns that scopes must NOT match (None = exclude none)

    Returns:
        Filtered list of scope strings

    Examples:
        >>> filter_scopes_by_pattern(
        ...     ["S3Bucket:a:read", "S3Bucket:b:read", "S3Bucket:a:write"],
        ...     exclude_patterns=["*:a:write"]
        ... )
        ["S3Bucket:a:read", "S3Bucket:b:read"]
    """
    filtered = list(scopes)

    # Apply inclusion patterns
    if include_patterns:
        filtered = [
            scope_str
            for scope_str in filtered
            if any(
                scope_matches(parse_scope(scope_str), parse_scope(pattern))
                for pattern in include_patterns
            )
        ]

    # Apply exclusion patterns
    if exclude_patterns:
        filtered = [
            scope_str
            for scope_str in filtered
            if not any(
                scope_matches(parse_scope(scope_str), parse_scope(pattern))
                for pattern in exclude_patterns
            )
        ]

    return filtered
