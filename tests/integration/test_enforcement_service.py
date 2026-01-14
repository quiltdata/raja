import pytest

from .helpers import issue_token, request_json


@pytest.mark.integration
def test_enforcement_allows_granted_scope():
    token, _ = issue_token("alice")
    status, body = request_json(
        "POST",
        "/authorize",
        {
            "token": token,
            "request": {
                "resource_type": "Document",
                "resource_id": "doc123",
                "action": "read",
            },
        },
    )
    assert status == 200
    assert body["allowed"] is True
    assert body["matched_scope"] == "Document:doc123:read"


@pytest.mark.integration
def test_enforcement_denies_missing_scope():
    token, _ = issue_token("alice")
    status, body = request_json(
        "POST",
        "/authorize",
        {
            "token": token,
            "request": {
                "resource_type": "Document",
                "resource_id": "doc123",
                "action": "delete",
            },
        },
    )
    assert status == 200
    assert body["allowed"] is False


@pytest.mark.integration
def test_enforcement_allows_admin_delete():
    token, _ = issue_token("admin")
    status, body = request_json(
        "POST",
        "/authorize",
        {
            "token": token,
            "request": {
                "resource_type": "Document",
                "resource_id": "doc123",
                "action": "delete",
            },
        },
    )
    assert status == 200
    assert body["allowed"] is True
