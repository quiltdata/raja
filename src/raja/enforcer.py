from __future__ import annotations

from .models import AuthRequest, Decision, Scope
from .scope import format_scope, is_subset
from .token import TokenValidationError, validate_token


def check_scopes(request: AuthRequest, granted_scopes: list[str]) -> bool:
    """Return True if the request scope is included in the granted scopes."""
    requested_scope = Scope(
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        action=request.action,
    )
    return is_subset(requested_scope, granted_scopes)


def enforce(token_str: str, request: AuthRequest, secret: str) -> Decision:
    """Enforce authorization by validating a token and checking scopes."""
    try:
        token = validate_token(token_str, secret)
    except TokenValidationError as exc:
        return Decision(allowed=False, reason=str(exc))
    except Exception:
        return Decision(allowed=False, reason="invalid token")

    try:
        allowed = check_scopes(request, token.scopes)
    except Exception:
        return Decision(allowed=False, reason="invalid request")

    requested_scope = format_scope(request.resource_type, request.resource_id, request.action)
    if allowed:
        return Decision(allowed=True, reason="scope matched", matched_scope=requested_scope)

    return Decision(allowed=False, reason="scope not granted")
