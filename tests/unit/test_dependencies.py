"""Tests for FastAPI dependency injection and AWS client caching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from raja.server import dependencies


@pytest.fixture(autouse=True)
def reset_caches() -> None:
    """Reset all module-level caches before each test."""
    dependencies._avp_client = None
    dependencies._dynamodb_resource = None
    dependencies._principal_table = None
    dependencies._mappings_table = None
    dependencies._audit_table = None
    dependencies._jwt_secret_cache = None
    dependencies._harness_secret_cache = None


def test_get_avp_client_caches_result() -> None:
    """Test that AVP client is created once and cached."""
    with (
        patch.dict("os.environ", {"AWS_REGION": "us-east-1"}),
        patch("boto3.client") as mock_client,
    ):
        mock_avp = MagicMock()
        mock_client.return_value = mock_avp

        # First call should create client
        client1 = dependencies.get_avp_client()
        assert client1 is mock_avp
        assert mock_client.call_count == 1

        # Second call should return cached client
        client2 = dependencies.get_avp_client()
        assert client2 is mock_avp
        assert mock_client.call_count == 1  # No additional calls


def test_get_dynamodb_resource_caches_result() -> None:
    """Test that DynamoDB resource is created once and cached."""
    with (
        patch.dict("os.environ", {"AWS_REGION": "us-east-1"}),
        patch("boto3.resource") as mock_resource,
    ):
        mock_dynamodb = MagicMock()
        mock_resource.return_value = mock_dynamodb

        # First call should create resource
        resource1 = dependencies.get_dynamodb_resource()
        assert resource1 is mock_dynamodb
        assert mock_resource.call_count == 1

        # Second call should return cached resource
        resource2 = dependencies.get_dynamodb_resource()
        assert resource2 is mock_dynamodb
        assert mock_resource.call_count == 1


def test_get_principal_table_requires_env_var() -> None:
    """Test that get_principal_table fails without PRINCIPAL_TABLE env var."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="PRINCIPAL_TABLE is required"):
            dependencies.get_principal_table()


def test_get_principal_table_caches_result() -> None:
    """Test that principal table is created once and cached."""
    mock_table = MagicMock()
    mock_resource = MagicMock()
    mock_resource.Table.return_value = mock_table

    with (
        patch.dict("os.environ", {"PRINCIPAL_TABLE": "test-principal-table"}),
        patch.object(dependencies, "get_dynamodb_resource", return_value=mock_resource),
    ):
        # First call should create table
        table1 = dependencies.get_principal_table()
        assert table1 is mock_table
        assert mock_resource.Table.call_count == 1

        # Second call should return cached table
        table2 = dependencies.get_principal_table()
        assert table2 is mock_table
        assert mock_resource.Table.call_count == 1


def test_get_mappings_table_requires_env_var() -> None:
    """Test that get_mappings_table fails without MAPPINGS_TABLE env var."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="MAPPINGS_TABLE is required"):
            dependencies.get_mappings_table()


def test_get_mappings_table_caches_result() -> None:
    """Test that mappings table is created once and cached."""
    mock_table = MagicMock()
    mock_resource = MagicMock()
    mock_resource.Table.return_value = mock_table

    with (
        patch.dict("os.environ", {"MAPPINGS_TABLE": "test-mappings-table"}),
        patch.object(dependencies, "get_dynamodb_resource", return_value=mock_resource),
    ):
        # First call should create table
        table1 = dependencies.get_mappings_table()
        assert table1 is mock_table
        assert mock_resource.Table.call_count == 1

        # Second call should return cached table
        table2 = dependencies.get_mappings_table()
        assert table2 is mock_table
        assert mock_resource.Table.call_count == 1


def test_get_audit_table_requires_env_var() -> None:
    """Test that get_audit_table fails without AUDIT_TABLE env var."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="AUDIT_TABLE is required"):
            dependencies.get_audit_table()


def test_get_audit_table_caches_result() -> None:
    """Test that audit table is created once and cached."""
    mock_table = MagicMock()
    mock_resource = MagicMock()
    mock_resource.Table.return_value = mock_table

    with (
        patch.dict("os.environ", {"AUDIT_TABLE": "test-audit-table"}),
        patch.object(dependencies, "get_dynamodb_resource", return_value=mock_resource),
    ):
        table1 = dependencies.get_audit_table()
        assert table1 is mock_table
        assert mock_resource.Table.call_count == 1

        table2 = dependencies.get_audit_table()
        assert table2 is mock_table
        assert mock_resource.Table.call_count == 1


def test_get_jwt_secret_requires_env_var() -> None:
    """Test that get_jwt_secret fails without JWT_SECRET_ARN env var."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="JWT_SECRET_ARN is required"):
            dependencies.get_jwt_secret()


def test_get_jwt_secret_caches_result() -> None:
    """Test that JWT secret is retrieved once and cached."""
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "test-jwt-secret"}

    with (
        patch.dict(
            "os.environ",
            {"JWT_SECRET_ARN": "arn:aws:secretsmanager:...", "AWS_REGION": "us-east-1"},
        ),
        patch("boto3.client", return_value=mock_client),
    ):
        # First call should retrieve secret
        secret1 = dependencies.get_jwt_secret()
        assert secret1 == "test-jwt-secret"
        assert mock_client.get_secret_value.call_count == 1

        # Second call should return cached secret
        secret2 = dependencies.get_jwt_secret()
        assert secret2 == "test-jwt-secret"
        assert mock_client.get_secret_value.call_count == 1


def test_get_harness_secret_prefers_env_var() -> None:
    """Test that get_harness_secret prefers RAJ_HARNESS_SECRET env var."""
    with patch.dict("os.environ", {"RAJ_HARNESS_SECRET": "local-secret"}):
        secret = dependencies.get_harness_secret()
        assert secret == "local-secret"


def test_get_harness_secret_falls_back_to_secrets_manager() -> None:
    """Test that get_harness_secret falls back to Secrets Manager."""
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "sm-secret"}

    with (
        patch.dict(
            "os.environ",
            {
                "HARNESS_SECRET_ARN": "arn:aws:secretsmanager:...",
                "AWS_REGION": "us-east-1",
            },
            clear=True,
        ),
        patch("boto3.client", return_value=mock_client),
    ):
        secret = dependencies.get_harness_secret()
        assert secret == "sm-secret"


def test_get_harness_secret_requires_either_env_var() -> None:
    """Test that get_harness_secret fails without any secret source."""
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="Either RAJ_HARNESS_SECRET or HARNESS_SECRET_ARN"):
            dependencies.get_harness_secret()


def test_get_harness_secret_caches_result() -> None:
    """Test that harness secret is retrieved once and cached."""
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "harness-secret"}

    with (
        patch.dict(
            "os.environ",
            {
                "HARNESS_SECRET_ARN": "arn:aws:secretsmanager:...",
                "AWS_REGION": "us-east-1",
            },
            clear=True,
        ),
        patch("boto3.client", return_value=mock_client),
    ):
        # First call should retrieve secret
        secret1 = dependencies.get_harness_secret()
        assert secret1 == "harness-secret"
        assert mock_client.get_secret_value.call_count == 1

        # Second call should return cached secret
        secret2 = dependencies.get_harness_secret()
        assert secret2 == "harness-secret"
        assert mock_client.get_secret_value.call_count == 1
