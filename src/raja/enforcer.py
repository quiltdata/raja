from __future__ import annotations

import structlog
from pydantic import ValidationError

from .exceptions import ScopeValidationError, TokenExpiredError, TokenInvalidError
from .models import AuthRequest, Decision, Scope
from .scope import format_scope, parse_scope
from .token import TokenValidationError, validate_token


def _matches_component(granted: str, requested: str) -> bool:
    if granted.endswith("/") or granted.endswith("-"):
        return requested.startswith(granted)
    return granted == requested


_MULTIPART_ACTIONS = {
    "s3:InitiateMultipartUpload",
    "s3:UploadPart",
    "s3:CompleteMultipartUpload",
    "s3:AbortMultipartUpload",
}


def _action_matches(granted_action: str, requested_action: str) -> bool:
    if granted_action == requested_action:
        return True
    if requested_action == "s3:HeadObject" and granted_action == "s3:GetObject":
        return True
    if requested_action in _MULTIPART_ACTIONS and granted_action == "s3:PutObject":
        return True
    return False


def is_prefix_match(granted_scope: str, requested_scope: str) -> bool:
    """Check if requested scope matches granted scope (with prefix matching)."""
    granted = parse_scope(granted_scope)
    requested = parse_scope(requested_scope)

    if granted.resource_type != requested.resource_type:
        return False
    if not _action_matches(granted.action, requested.action):
        return False

    if granted.resource_type == "S3Object":
        if "/" not in granted.resource_id or "/" not in requested.resource_id:
            return False
        granted_bucket, granted_key = granted.resource_id.split("/", 1)
        requested_bucket, requested_key = requested.resource_id.split("/", 1)
        return _matches_component(granted_bucket, requested_bucket) and _matches_component(
            granted_key, requested_key
        )

    if granted.resource_type == "S3Bucket":
        return _matches_component(granted.resource_id, requested.resource_id)

    return _matches_component(granted.resource_id, requested.resource_id)


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
        requested_scope_str = format_scope(
            requested_scope.resource_type,
            requested_scope.resource_id,
            requested_scope.action,
        )
        return any(
            is_prefix_match(granted_scope, requested_scope_str) for granted_scope in granted_scopes
        )
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
