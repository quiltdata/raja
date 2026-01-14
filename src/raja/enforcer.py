from __future__ import annotations

import structlog
from pydantic import ValidationError

from .exceptions import ScopeValidationError, TokenExpiredError, TokenInvalidError
from .models import AuthRequest, Decision, Scope
from .scope import format_scope, is_subset
from .token import TokenValidationError, validate_token

logger = structlog.get_logger(__name__)


def check_scopes(request: AuthRequest, granted_scopes: list[str]) -> bool:
    """Return True if the request scope is included in the granted scopes.

    Args:
        request: Authorization request containing resource and action
        granted_scopes: List of scope strings granted to the principal

    Returns:
        True if the requested scope is a subset of granted scopes, False otherwise

    Raises:
        ScopeValidationError: If scope validation fails
        ValidationError: If the request or scope data is invalid
    """
    try:
        requested_scope = Scope(
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            action=request.action,
        )
    except ValidationError as exc:
        logger.warning("scope_validation_failed", error=str(exc))
        raise ScopeValidationError(f"invalid scope data: {exc}") from exc
    except Exception as exc:
        logger.error("unexpected_scope_creation_error", error=str(exc), exc_info=True)
        raise ScopeValidationError(f"unexpected error creating scope: {exc}") from exc

    try:
        return is_subset(requested_scope, granted_scopes)
    except Exception as exc:
        logger.error("scope_subset_check_failed", error=str(exc), exc_info=True)
        raise ScopeValidationError(f"failed to check scope subset: {exc}") from exc


def enforce(token_str: str, request: AuthRequest, secret: str) -> Decision:
    """Enforce authorization by validating a token and checking scopes.

    Args:
        token_str: JWT token string to validate
        request: Authorization request containing resource and action
        secret: Secret key used to verify the token signature

    Returns:
        Decision indicating whether the request is allowed or denied

    Note:
        This function follows a fail-closed design - any errors result in DENY.
        All exceptions are caught and logged with appropriate detail level.
    """
    # Validate token
    try:
        token = validate_token(token_str, secret)
    except TokenExpiredError as exc:
        logger.warning("token_expired_in_enforce", error=str(exc))
        return Decision(allowed=False, reason="token expired")
    except TokenInvalidError as exc:
        logger.warning("token_invalid_in_enforce", error=str(exc))
        return Decision(allowed=False, reason="invalid token")
    except TokenValidationError as exc:
        logger.warning("token_validation_failed_in_enforce", error=str(exc))
        return Decision(allowed=False, reason=str(exc))
    except Exception as exc:
        logger.error("unexpected_token_error_in_enforce", error=str(exc), exc_info=True)
        return Decision(allowed=False, reason="internal error during token validation")

    # Check scopes
    try:
        allowed = check_scopes(request, token.scopes)
    except ScopeValidationError as exc:
        logger.warning("scope_validation_failed_in_enforce", error=str(exc))
        return Decision(allowed=False, reason="invalid request scope")
    except ValidationError as exc:
        logger.warning("request_validation_failed_in_enforce", error=str(exc))
        return Decision(allowed=False, reason="invalid request")
    except Exception as exc:
        logger.error("unexpected_scope_error_in_enforce", error=str(exc), exc_info=True)
        return Decision(allowed=False, reason="internal error during scope checking")

    requested_scope = format_scope(request.resource_type, request.resource_id, request.action)
    if allowed:
        logger.info(
            "authorization_allowed",
            principal=token.subject,
            resource_type=request.resource_type,
            resource_id=request.resource_id,
            action=request.action,
            requested_scope=requested_scope,
        )
        return Decision(allowed=True, reason="scope matched", matched_scope=requested_scope)

    logger.warning(
        "authorization_denied",
        principal=token.subject,
        resource_type=request.resource_type,
        resource_id=request.resource_id,
        action=request.action,
        requested_scope=requested_scope,
        granted_scopes_count=len(token.scopes),
    )
    return Decision(allowed=False, reason="scope not granted")
