import pytest

from .helpers import issue_token, request_json


@pytest.mark.integration
def test_end_to_end_token_authorize_introspect():
    token, scopes = issue_token("alice")

    status, auth_body = request_json(
        "POST",
        "/authorize",
        {
            "token": token,
            "request": {
                "resource_type": "Document",
                "resource_id": "doc123",
                "action": "write",
            },
        },
    )
    assert status == 200
    assert auth_body["allowed"] is True

    status, introspect_body = request_json("GET", "/introspect", query={"token": token})
    assert status == 200
    claims = introspect_body["claims"]
    assert claims["sub"] == "alice"
    assert set(claims["scopes"]) == set(scopes)
