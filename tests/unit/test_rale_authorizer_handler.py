from __future__ import annotations

from typing import Any

import pytest

from lambda_handlers.rale_authorizer.handler import (
    _extract_principal,
    _normalize_iam_principal,
    handler,
)


def test_normalize_assumed_role_to_iam_role() -> None:
    assumed_role = "arn:aws:sts::123456789012:assumed-role/path/to/role/session-name"

    assert _normalize_iam_principal(assumed_role) == "arn:aws:iam::123456789012:role/path/to/role"


def test_extract_principal_prefers_trusted_forwarded_jwt_subject(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "RAJA_TRUSTED_FORWARDER_ARNS",
        "arn:aws:iam::123456789012:role/raja-rajee-task",
    )
    event = {
        "headers": {"x-raja-jwt-payload": '{"sub":"arn:aws:iam::123456789012:user/alice"}'},
        "requestContext": {
            "authorizer": {
                "iam": {
                    "userArn": (
                        "arn:aws:sts::123456789012:assumed-role/raja-rajee-task/session-123"
                    )
                }
            }
        },
    }

    assert _extract_principal(event) == "arn:aws:iam::123456789012:user/alice"


def test_extract_principal_uses_caller_arn_for_untrusted_forwarder(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAJA_TRUSTED_FORWARDER_ARNS", raising=False)
    event = {
        "headers": {"x-raja-principal": "arn:aws:iam::123456789012:user/alice"},
        "requestContext": {
            "authorizer": {
                "iam": {
                    "userArn": ("arn:aws:sts::123456789012:assumed-role/untrusted-role/session-123")
                }
            }
        },
    }

    assert _extract_principal(event) == "arn:aws:iam::123456789012:role/untrusted-role"


def test_extract_principal_allows_asserted_header_only_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RAJA_ALLOW_ASSERTED_PRINCIPAL", "true")
    event = {"headers": {"x-raja-principal": "arn:aws:iam::123456789012:user/alice"}}

    assert _extract_principal(event) == "arn:aws:iam::123456789012:user/alice"


def test_extract_principal_rejects_unproven_asserted_header(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAJA_ALLOW_ASSERTED_PRINCIPAL", raising=False)
    monkeypatch.delenv("RAJA_TRUSTED_FORWARDER_ARNS", raising=False)
    event = {"headers": {"x-raja-principal": "arn:aws:iam::123456789012:user/alice"}}

    with pytest.raises(ValueError, match="IAM identity or trusted forwarded identity"):
        _extract_principal(event)


def test_handler_health_endpoint() -> None:
    assert handler({"rawPath": "/health"}, context=None)["statusCode"] == 200


def test_handler_denied_response_omits_package_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("JWT_SECRET_ARN", "arn:aws:secretsmanager:us-east-1:123:secret:test")
    monkeypatch.setenv("AWS_REGION", "us-east-1")
    monkeypatch.setenv("DATAZONE_DOMAIN_ID", "dzd_123")
    monkeypatch.setenv("DATAZONE_OWNER_PROJECT_ID", "proj-owner")
    monkeypatch.setenv("DATAZONE_USERS_PROJECT_ID", "proj-users")
    monkeypatch.setenv("DATAZONE_GUESTS_PROJECT_ID", "proj-guests")

    class FakeConfig:
        @staticmethod
        def from_env() -> FakeConfig:
            return FakeConfig()

        def ordered_projects(self) -> list[tuple[str, Any]]:
            project = type("Project", (), {"project_id": "proj-users"})()
            return [("users", project)]

    class FakeService:
        def __init__(self, client: Any, config: Any) -> None:
            _ = client
            _ = config

        def find_project_for_principal(self, principal: str, project_ids: list[str]) -> str:
            assert principal == "arn:aws:iam::123456789012:user/alice"
            assert project_ids == ["proj-users"]
            return "proj-users"

        def has_package_grant(self, project_id: str, quilt_uri: str) -> bool:
            assert project_id == "proj-users"
            assert quilt_uri == "quilt+s3://registry#package=author/pkg@hash123"
            return False

    monkeypatch.setattr("lambda_handlers.rale_authorizer.handler.DataZoneConfig", FakeConfig)
    monkeypatch.setattr("lambda_handlers.rale_authorizer.handler.DataZoneService", FakeService)

    class FakeBoto3:
        @staticmethod
        def client(service: str, region_name: str) -> object:
            assert region_name == "us-east-1"
            return object()

    monkeypatch.setattr("lambda_handlers.rale_authorizer.handler.boto3", FakeBoto3)

    response = handler(
        {
            "rawPath": "/registry/author/pkg@hash123/data.csv",
            "headers": {},
            "requestContext": {
                "authorizer": {"iam": {"userArn": "arn:aws:iam::123456789012:user/alice"}}
            },
        },
        context=None,
    )

    assert response["statusCode"] == 403
    assert response["body"] == '{"decision": "DENY"}'
