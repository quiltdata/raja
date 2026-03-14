"""Tests for FastAPI dependency injection and AWS client caching."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from raja.server import dependencies


@pytest.fixture(autouse=True)
def reset_caches() -> None:
    """Reset all module-level caches before each test."""
    dependencies._datazone_client = None
    dependencies._jwt_secret_cache = None


def test_get_datazone_client_caches_result() -> None:
    with (
        patch.dict("os.environ", {"AWS_REGION": "us-east-1"}),
        patch("boto3.client") as mock_client,
    ):
        mock_datazone = MagicMock()
        mock_client.return_value = mock_datazone

        client1 = dependencies.get_datazone_client()
        assert client1 is mock_datazone
        assert mock_client.call_count == 1

        client2 = dependencies.get_datazone_client()
        assert client2 is mock_datazone
        assert mock_client.call_count == 1


def test_get_jwt_secret_requires_env_var() -> None:
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(RuntimeError, match="JWT_SECRET_ARN is required"):
            dependencies.get_jwt_secret()


def test_get_jwt_secret_caches_result() -> None:
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "test-jwt-secret"}

    with (
        patch.dict(
            "os.environ",
            {"JWT_SECRET_ARN": "arn:aws:secretsmanager:...", "AWS_REGION": "us-east-1"},
        ),
        patch("boto3.client", return_value=mock_client),
    ):
        secret1 = dependencies.get_jwt_secret()
        assert secret1 == "test-jwt-secret"
        assert mock_client.get_secret_value.call_count == 1

        secret2 = dependencies.get_jwt_secret()
        assert secret2 == "test-jwt-secret"
        assert mock_client.get_secret_value.call_count == 1


def test_get_jwt_secret_uses_version_when_configured() -> None:
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": "test-jwt-secret-v2"}

    with (
        patch.dict(
            "os.environ",
            {
                "JWT_SECRET_ARN": "arn:aws:secretsmanager:...",
                "JWT_SECRET_VERSION": "version-2",
                "AWS_REGION": "us-east-1",
            },
        ),
        patch("boto3.client", return_value=mock_client),
    ):
        secret = dependencies.get_jwt_secret()
        assert secret == "test-jwt-secret-v2"
        mock_client.get_secret_value.assert_called_once_with(
            SecretId="arn:aws:secretsmanager:...",
            VersionId="version-2",
        )
