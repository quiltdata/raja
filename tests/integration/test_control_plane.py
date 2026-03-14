import pytest

from .helpers import request_json, require_raja_users, require_test_principal


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
    """Verify that seeded IAM users appear in /principals."""
    status, body = request_json("GET", "/principals")
    assert status == 200
    seeded_arns = set(require_raja_users())
    present = {item.get("principal") for item in body.get("principals", [])}
    missing = seeded_arns - present
    assert not missing, (
        f"Expected IAM user principal(s) not found in DynamoDB: {missing}\n"
        "Run: python scripts/seed_test_data.py"
    )


@pytest.mark.integration
def test_control_plane_audit_log_entries():
    principal = require_test_principal()
    token_status, _ = request_json("POST", "/token", {"principal": principal})
    assert token_status == 200

    status, body = request_json(
        "GET",
        "/audit",
        query={"principal": principal, "limit": "10"},
    )
    assert status == 200
    entries = body.get("entries", [])
    assert any(entry.get("principal") == principal for entry in entries)
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
    unknown = "arn:aws:iam::000000000000:user/nobody"
    request_json("POST", "/token", {"principal": unknown})
    status, body = request_json(
        "GET",
        "/audit",
        query={"principal": unknown, "limit": "10"},
    )
    assert status == 200
    entries = body.get("entries", [])
    assert any(entry.get("decision") == "DENY" for entry in entries)
