import pytest

from .helpers import request_json


@pytest.mark.integration
def test_control_plane_compiles_policies():
    status, body = request_json("POST", "/compile")
    assert status == 200
    assert body.get("policies_compiled", 0) >= 1


@pytest.mark.integration
def test_control_plane_lists_principals():
    status, body = request_json("GET", "/principals")
    assert status == 200
    principals = {item.get("principal") for item in body.get("principals", [])}
    assert {"test-user"}.issubset(principals)


@pytest.mark.integration
def test_control_plane_lists_policies():
    status, body = request_json("GET", "/policies")
    assert status == 200
    assert len(body.get("policies", [])) >= 1


@pytest.mark.integration
def test_control_plane_audit_log_entries():
    request_json("POST", "/compile")
    token_status, _ = request_json("POST", "/token", {"principal": "test-user"})
    assert token_status == 200

    status, body = request_json(
        "GET",
        "/audit",
        query={"principal": "test-user", "limit": "10"},
    )
    assert status == 200
    entries = body.get("entries", [])
    assert any(entry.get("principal") == "test-user" for entry in entries)
    for entry in entries[:1]:
        for field in [
            "timestamp",
            "principal",
            "action",
            "resource",
            "decision",
            "policy_store_id",
            "request_id",
        ]:
            assert field in entry
