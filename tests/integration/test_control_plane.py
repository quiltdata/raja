import pytest

from .helpers import request_json


@pytest.mark.integration
def test_control_plane_policies_loaded_to_avp():
    """Verify that policies have been loaded to AVP."""
    status, body = request_json("GET", "/policies")
    assert status == 200
    policies = body.get("policies", [])
    assert len(policies) >= 1, "No policies found in AVP. Run ./poe load-policies first."


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


@pytest.mark.integration
def test_control_plane_audit_logs_denied_token_requests():
    request_json("POST", "/token", {"principal": "unknown-user"})
    status, body = request_json(
        "GET",
        "/audit",
        query={"principal": "unknown-user", "limit": "10"},
    )
    assert status == 200
    entries = body.get("entries", [])
    assert any(entry.get("decision") == "DENY" for entry in entries)
