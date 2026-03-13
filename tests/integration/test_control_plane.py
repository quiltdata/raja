import pytest

from .helpers import request_json


@pytest.mark.integration
def test_control_plane_lists_datazone_package_listings():
    """Verify that DataZone-backed package listings are visible via /policies."""
    status, body = request_json("GET", "/policies")
    assert status == 200
    policies = body.get("policies", [])
    assert len(policies) >= 1, (
        "No DataZone package listings found. Run python scripts/seed_packages.py"
    )


@pytest.mark.integration
def test_control_plane_lists_principals():
    status, body = request_json("GET", "/principals")
    assert status == 200
    principals = {item.get("principal") for item in body.get("principals", [])}
    assert {"test-user"}.issubset(principals)


@pytest.mark.integration
def test_control_plane_audit_log_entries():
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
            "authorization_plane_id",
            "request_id",
        ]:
            assert field in entry
        assert str(entry["authorization_plane_id"]).startswith("datazone:")


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
