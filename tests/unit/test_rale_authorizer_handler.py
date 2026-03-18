from __future__ import annotations

import pytest

from lambda_handlers.rale_authorizer.handler import _extract_principal, _normalize_iam_principal


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
