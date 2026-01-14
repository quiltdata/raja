from __future__ import annotations

import time
from typing import Any, cast

import jwt

from .models import Token


class TokenValidationError(ValueError):
    pass


def create_token(subject: str, scopes: list[str], ttl: int, secret: str) -> str:
    """Create a signed JWT containing scope claims."""
    issued_at = int(time.time())
    expires_at = issued_at + ttl
    payload = {
        "sub": subject,
        "scopes": scopes,
        "iat": issued_at,
        "exp": expires_at,
    }
    return jwt.encode(payload, secret, algorithm="HS256")


def validate_token(token_str: str, secret: str) -> Token:
    """Validate a JWT signature and return the decoded Token model."""
    try:
        payload = jwt.decode(token_str, secret, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise TokenValidationError("token expired") from exc
    except jwt.InvalidTokenError as exc:
        raise TokenValidationError("invalid token") from exc

    return Token(
        subject=payload.get("sub", ""),
        scopes=list(payload.get("scopes", [])),
        issued_at=int(payload.get("iat", 0)),
        expires_at=int(payload.get("exp", 0)),
    )


def decode_token(token_str: str) -> dict[str, Any]:
    """Decode a JWT without validating signature or expiration."""
    payload = jwt.decode(
        token_str,
        options={"verify_signature": False, "verify_exp": False},
        algorithms=["HS256"],
    )
    return cast(dict[str, Any], payload)


def is_expired(token: Token) -> bool:
    """Return True if the token is expired relative to current time."""
    return int(time.time()) >= token.expires_at
