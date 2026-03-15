"""FastAPI dependencies for AWS clients and resources.

This module provides cached AWS client initialization for efficient
Lambda execution. Clients are created once per Lambda container and
reused across invocations.
"""

from __future__ import annotations

import os
import secrets
from typing import Any

import boto3
from fastapi import HTTPException, Request

# Module-level caches (initialized once per Lambda container)
_datazone_client: Any | None = None
_jwt_secret_cache: dict[str, str] | None = None


def _get_region() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise RuntimeError("AWS_REGION is required")
    return region


def _require_env(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def get_datazone_client() -> Any:
    """Get cached Amazon DataZone client."""
    global _datazone_client
    if _datazone_client is None:
        _datazone_client = boto3.client("datazone", region_name=_get_region())
    return _datazone_client


def get_jwt_secret() -> str:
    """Get JWT signing secret from AWS Secrets Manager."""
    global _jwt_secret_cache

    secret_arn = _require_env(os.environ.get("JWT_SECRET_ARN"), "JWT_SECRET_ARN")
    secret_version = os.environ.get("JWT_SECRET_VERSION")
    cache_key = f"{secret_arn}:{secret_version or ''}"
    if _jwt_secret_cache is not None and cache_key in _jwt_secret_cache:
        return _jwt_secret_cache[cache_key]

    client = boto3.client("secretsmanager", region_name=_get_region())
    get_secret_kwargs: dict[str, str] = {"SecretId": secret_arn}
    if secret_version:
        get_secret_kwargs["VersionId"] = secret_version
    response = client.get_secret_value(**get_secret_kwargs)
    secret = response.get("SecretString")
    if not isinstance(secret, str) or not secret:
        raise RuntimeError("Secrets Manager returned an invalid SecretString")
    if _jwt_secret_cache is None:
        _jwt_secret_cache = {}
    _jwt_secret_cache[cache_key] = secret
    return secret


def require_admin_auth(request: Request) -> None:
    """Require a valid admin bearer token in the Authorization header."""
    auth_header = request.headers.get("Authorization")
    if not auth_header:
        raise HTTPException(status_code=401, detail="Missing or malformed authorization header")

    scheme, _, token = auth_header.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="Missing or malformed authorization header")

    admin_key = os.environ.get("ADMIN_KEY") or os.environ.get("RAJA_ADMIN_KEY")
    if not admin_key:
        raise HTTPException(status_code=500, detail="ADMIN_KEY is not configured")

    if not secrets.compare_digest(token, admin_key):
        raise HTTPException(status_code=401, detail="Invalid or missing admin key")
