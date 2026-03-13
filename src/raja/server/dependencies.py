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
_dynamodb_resource: Any | None = None
_principal_table: Any | None = None
_audit_table: Any | None = None
_jwt_secret_cache: dict[str, str] | None = None


def _get_region() -> str:
    """Get AWS region from environment variables.

    Returns:
        AWS region name

    Raises:
        RuntimeError: If AWS_REGION is not set
    """
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise RuntimeError("AWS_REGION is required")
    return region


def _require_env(value: str | None, name: str) -> str:
    """Ensure environment variable is set.

    Args:
        value: Environment variable value
        name: Environment variable name

    Returns:
        Environment variable value

    Raises:
        RuntimeError: If environment variable is not set
    """
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def get_datazone_client() -> Any:
    """Get cached Amazon DataZone client."""
    global _datazone_client
    if _datazone_client is None:
        _datazone_client = boto3.client("datazone", region_name=_get_region())
    return _datazone_client


def get_dynamodb_resource() -> Any:
    """Get cached DynamoDB resource.

    Creates the resource on first call and reuses it for subsequent calls
    within the same Lambda container.

    Returns:
        boto3 dynamodb resource
    """
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb", region_name=_get_region())
    return _dynamodb_resource


def get_principal_table() -> Any:
    """Get cached DynamoDB principal scopes table.

    Returns:
        boto3 DynamoDB Table resource for principal scopes

    Raises:
        RuntimeError: If PRINCIPAL_TABLE environment variable is not set
    """
    global _principal_table
    if _principal_table is None:
        table_name = _require_env(os.environ.get("PRINCIPAL_TABLE"), "PRINCIPAL_TABLE")
        _principal_table = get_dynamodb_resource().Table(table_name)
    return _principal_table


def get_audit_table() -> Any:
    """Get cached DynamoDB audit log table.

    Returns:
        boto3 DynamoDB Table resource for audit log entries

    Raises:
        RuntimeError: If AUDIT_TABLE environment variable is not set
    """
    global _audit_table
    if _audit_table is None:
        table_name = _require_env(os.environ.get("AUDIT_TABLE"), "AUDIT_TABLE")
        _audit_table = get_dynamodb_resource().Table(table_name)
    return _audit_table


def get_jwt_secret() -> str:
    """Get JWT signing secret from AWS Secrets Manager.

    Retrieves the secret on first call and caches it for subsequent calls
    within the same Lambda container.

    Returns:
        JWT signing secret string

    Raises:
        RuntimeError: If JWT_SECRET_ARN environment variable is not set
    """
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
    secret = response["SecretString"]
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
