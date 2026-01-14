"""FastAPI dependencies for AWS clients and resources.

This module provides cached AWS client initialization for efficient
Lambda execution. Clients are created once per Lambda container and
reused across invocations.
"""

from __future__ import annotations

import os
from typing import Any

import boto3

# Module-level caches (initialized once per Lambda container)
_avp_client: Any | None = None
_dynamodb_resource: Any | None = None
_principal_table: Any | None = None
_mappings_table: Any | None = None
_jwt_secret_cache: str | None = None
_harness_secret_cache: str | None = None


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


def get_avp_client() -> Any:
    """Get cached Amazon Verified Permissions client.

    Creates the client on first call and reuses it for subsequent calls
    within the same Lambda container.

    Returns:
        boto3 verifiedpermissions client
    """
    global _avp_client
    if _avp_client is None:
        _avp_client = boto3.client("verifiedpermissions", region_name=_get_region())
    return _avp_client


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


def get_mappings_table() -> Any:
    """Get cached DynamoDB policy-scope mappings table.

    Returns:
        boto3 DynamoDB Table resource for policy-scope mappings

    Raises:
        RuntimeError: If MAPPINGS_TABLE environment variable is not set
    """
    global _mappings_table
    if _mappings_table is None:
        table_name = _require_env(os.environ.get("MAPPINGS_TABLE"), "MAPPINGS_TABLE")
        _mappings_table = get_dynamodb_resource().Table(table_name)
    return _mappings_table


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
    if _jwt_secret_cache is not None:
        return _jwt_secret_cache

    secret_arn = _require_env(os.environ.get("JWT_SECRET_ARN"), "JWT_SECRET_ARN")
    client = boto3.client("secretsmanager", region_name=_get_region())
    response = client.get_secret_value(SecretId=secret_arn)
    _jwt_secret_cache = response["SecretString"]
    return _jwt_secret_cache


def get_harness_secret() -> str:
    """Get S3 harness signing secret.

    First checks RAJ_HARNESS_SECRET environment variable (for local dev).
    If not set, loads from AWS Secrets Manager using HARNESS_SECRET_ARN.

    Returns:
        S3 harness signing secret string

    Raises:
        RuntimeError: If neither RAJ_HARNESS_SECRET nor HARNESS_SECRET_ARN is set
    """
    global _harness_secret_cache
    if _harness_secret_cache is not None:
        return _harness_secret_cache

    # Try environment variable first (for local development)
    secret = os.environ.get("RAJ_HARNESS_SECRET")
    if secret:
        _harness_secret_cache = secret
        return _harness_secret_cache

    # Fall back to Secrets Manager (production)
    harness_secret_arn = os.environ.get("HARNESS_SECRET_ARN")
    if not harness_secret_arn:
        raise RuntimeError(
            "Either RAJ_HARNESS_SECRET or HARNESS_SECRET_ARN environment variable is required"
        )

    client = boto3.client("secretsmanager", region_name=_get_region())
    response = client.get_secret_value(SecretId=harness_secret_arn)
    _harness_secret_cache = response["SecretString"]
    return _harness_secret_cache
