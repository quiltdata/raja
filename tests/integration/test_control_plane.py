import pytest

from .helpers import request_json, require_raja_users


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
        f"Expected IAM user principal(s) not found in DataZone projects: {missing}\n"
        "Run: python scripts/seed_users.py"
    )
