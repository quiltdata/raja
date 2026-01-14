from __future__ import annotations

import os
from typing import Any

import jwt
import structlog
from fastapi import FastAPI, Request

from raja.rajee.authorizer import construct_request_string, extract_bearer_token, is_authorized

logger = structlog.get_logger(__name__)

app = FastAPI()

_jwt_secret_cache: str | None = None


def get_jwt_secret() -> str:
    """Load the JWT signing secret once and cache it."""
    global _jwt_secret_cache
    if _jwt_secret_cache is not None:
        return _jwt_secret_cache

    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET not set")
    _jwt_secret_cache = secret
    return secret


@app.post("/authorize")
async def authorize(request: Request) -> dict[str, Any]:
    """Envoy ext_authz handler for S3 prefix authorization."""
    try:
        body = await request.json()
    except Exception as exc:  # pragma: no cover - FastAPI handles bad JSON at runtime
        logger.warning("authz_invalid_json", error=str(exc))
        return {
            "result": {
                "allowed": False,
                "status": {"code": 16, "message": "Invalid JSON body"},
            }
        }

    http_request = body.get("attributes", {}).get("request", {}).get("http", {})
    method = http_request.get("method", "")
    path = http_request.get("path", "")
    headers = http_request.get("headers", {}) or {}
    query = http_request.get("query_params", {}) or {}

    auth_header = headers.get("authorization", "")

    logger.info("authz_request", method=method, path=path)

    try:
        token = extract_bearer_token(auth_header)
        secret = get_jwt_secret()
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_exp": True})

        grants = payload.get("grants", [])
        if not isinstance(grants, list):
            raise ValueError("Invalid grants claim")

        request_string = construct_request_string(method, path, query)

        if is_authorized(request_string, grants):
            return {"result": {"allowed": True}}

        return {
            "result": {
                "allowed": False,
                "status": {"code": 7, "message": "Request not covered by any grant"},
            }
        }

    except jwt.ExpiredSignatureError:
        logger.warning("token_expired")
        return {
            "result": {
                "allowed": False,
                "status": {"code": 16, "message": "Token expired"},
            }
        }
    except (jwt.InvalidTokenError, ValueError) as exc:
        logger.warning("authorization_error", error=str(exc))
        return {
            "result": {
                "allowed": False,
                "status": {"code": 16, "message": "Invalid token or request"},
            }
        }
    except Exception as exc:
        logger.error("authorizer_error", error=str(exc), exc_info=True)
        return {
            "result": {
                "allowed": False,
                "status": {"code": 13, "message": "Internal authorization error"},
            }
        }


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}
