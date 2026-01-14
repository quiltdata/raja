import pytest

from .helpers import issue_token, request_json


@pytest.mark.integration
def test_token_service_issues_token_for_known_principal():
    token, scopes = issue_token("alice")
    assert token
    assert set(scopes) == {"Document:doc123:read", "Document:doc123:write"}


@pytest.mark.integration
def test_token_service_rejects_unknown_principal():
    status, body = request_json("POST", "/token", {"principal": "unknown-user"})
    assert status == 404
    assert "error" in body
