import pytest

from raja.models import AuthRequest, Scope, Token


def test_scope_validation_rejects_empty():
    with pytest.raises(ValueError):
        Scope(resource_type="", resource_id="doc", action="read")


def test_scope_validation_rejects_colon():
    with pytest.raises(ValueError):
        Scope(resource_type="S3Object", resource_id="bucket:key", action="s3:GetObject")


def test_auth_request_validation():
    request = AuthRequest(
        resource_type="S3Object", resource_id="analytics-data/report.csv", action="s3:GetObject"
    )
    assert request.resource_type == "S3Object"


def test_token_requires_subject():
    with pytest.raises(ValueError):
        Token(
            subject=" ",
            scopes=["S3Object:analytics-data/report.csv:s3:GetObject"],
            issued_at=1,
            expires_at=2,
        )
