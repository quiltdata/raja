from __future__ import annotations

import time
from typing import Any, cast

import jwt
import structlog

from .exceptions import TokenExpiredError, TokenInvalidError, TokenValidationError
from .models import PackageToken, Token
from .quilt_uri import validate_quilt_uri

logger = structlog.get_logger(__name__)


def create_token(
    subject: str,
    scopes: list[str],
    ttl: int,
    secret: str,
    issuer: str | None = None,
    audience: str | list[str] | None = None,
) -> str:
    """Create a signed JWT containing scope claims."""
    issued_at = int(time.time())
    expires_at = issued_at + ttl
    payload = {
        "sub": subject,
        "scopes": scopes,
        "iat": issued_at,
        "exp": expires_at,
    }
    if issuer:
        payload["iss"] = issuer
    if audience:
        payload["aud"] = audience
    return jwt.encode(payload, secret, algorithm="HS256")


def create_token_with_grants(
    subject: str,
    grants: list[str],
    ttl: int,
    secret: str,
    issuer: str | None = None,
    audience: str | list[str] | None = None,
) -> str:
    """Create a signed JWT containing grant claims."""
    issued_at = int(time.time())
    expires_at = issued_at + ttl
    payload = {
        "sub": subject,
        "grants": grants,
        "iat": issued_at,
        "exp": expires_at,
    }
    if issuer:
        payload["iss"] = issuer
    if audience:
        payload["aud"] = audience
    return jwt.encode(payload, secret, algorithm="HS256")


def create_token_with_package_grant(
    subject: str,
    quilt_uri: str,
    mode: str,
    ttl: int,
    secret: str,
    issuer: str | None = None,
    audience: str | list[str] | None = None,
) -> str:
    """Create a signed JWT containing a package grant."""
    issued_at = int(time.time())
    expires_at = issued_at + ttl
    payload = {
        "sub": subject,
        "quilt_uri": quilt_uri,
        "mode": mode,
        "iat": issued_at,
        "exp": expires_at,
    }
    if issuer:
        payload["iss"] = issuer
    if audience:
        payload["aud"] = audience
    return jwt.encode(payload, secret, algorithm="HS256")


def validate_package_token(token_str: str, secret: str) -> PackageToken:
    """Validate a JWT signature and return a decoded PackageToken model."""
    try:
        payload = jwt.decode(token_str, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        logger.warning("package_token_expired", error=str(exc))
        raise TokenExpiredError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("package_token_invalid", error=str(exc))
        raise TokenInvalidError("invalid token") from exc
    except Exception as exc:
        logger.error("unexpected_package_token_validation_error", error=str(exc), exc_info=True)
        raise TokenValidationError(f"unexpected token validation error: {exc}") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise TokenValidationError("token subject is required")

    quilt_uri = payload.get("quilt_uri")
    if not isinstance(quilt_uri, str) or not quilt_uri.strip():
        raise TokenValidationError("token quilt_uri is required")

    try:
        quilt_uri = validate_quilt_uri(quilt_uri)
    except ValueError as exc:
        raise TokenValidationError(f"invalid quilt uri: {exc}") from exc

    mode = payload.get("mode")
    if mode not in {"read", "readwrite"}:
        raise TokenValidationError("token mode must be 'read' or 'readwrite'")

    try:
        return PackageToken(
            subject=subject,
            quilt_uri=quilt_uri,
            mode=mode,
            issued_at=int(payload.get("iat", 0)),
            expires_at=int(payload.get("exp", 0)),
        )
    except Exception as exc:
        logger.error("package_token_model_creation_failed", error=str(exc), exc_info=True)
        raise TokenValidationError(f"failed to create token model: {exc}") from exc


def validate_token(token_str: str, secret: str) -> Token:
    """Validate a JWT signature and return the decoded Token model.

    Args:
        token_str: JWT token string to validate
        secret: Secret key used to verify the token signature

    Returns:
        Token model with decoded claims

    Raises:
        TokenExpiredError: If the token has expired
        TokenInvalidError: If the token is malformed or signature is invalid
        TokenValidationError: If token validation fails for other reasons
    """
    try:
        payload = jwt.decode(token_str, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        logger.warning("token_expired", error=str(exc))
        raise TokenExpiredError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        logger.warning("token_invalid", error=str(exc))
        raise TokenInvalidError("invalid token") from exc
    except Exception as exc:
        logger.error("unexpected_token_validation_error", error=str(exc), exc_info=True)
        raise TokenValidationError(f"unexpected token validation error: {exc}") from exc

    subject = payload.get("sub")
    if not isinstance(subject, str) or not subject.strip():
        raise TokenValidationError("token subject is required")

    scopes = payload.get("scopes")
    if scopes is None:
        raise TokenValidationError("token scopes are required")
    if not isinstance(scopes, list):
        raise TokenValidationError("token scopes must be a list")

    try:
        return Token(
            subject=subject,
            scopes=scopes,
            issued_at=int(payload.get("iat", 0)),
            expires_at=int(payload.get("exp", 0)),
        )
    except Exception as exc:
        logger.error("token_model_creation_failed", error=str(exc), exc_info=True)
        raise TokenValidationError(f"failed to create token model: {exc}") from exc


def decode_token(token_str: str) -> dict[str, Any]:
    """Decode a JWT without validating signature or expiration.

    Args:
        token_str: JWT token string to decode

    Returns:
        Dictionary containing the decoded JWT payload

    Raises:
        TokenInvalidError: If the token cannot be decoded
    """
    try:
        payload = jwt.decode(
            token_str,
            options={"verify_signature": False, "verify_exp": False},
            algorithms=["HS256"],
        )
        return cast(dict[str, Any], payload)
    except jwt.InvalidTokenError as exc:
        logger.warning("token_decode_failed", error=str(exc))
        raise TokenInvalidError(f"failed to decode token: {exc}") from exc
    except Exception as exc:
        logger.error("unexpected_decode_error", error=str(exc), exc_info=True)
        raise TokenInvalidError(f"unexpected error decoding token: {exc}") from exc


def is_expired(token: Token) -> bool:
    """Return True if the token is expired relative to current time."""
    return int(time.time()) >= token.expires_at
