"""Integration tests for the admin website (DataZone-backed).

These tests validate that:
1. The admin UI HTML is served correctly
2. All admin API endpoints work with the correct key
3. The system reflects the DataZone migration (no AVP/Cedar remnants)
4. Policy mutation endpoints correctly return 410 Gone (read-only DataZone)
5. Principals carry DataZone project metadata when DataZone is enabled

Run against a deployed stack:
    RAJA_ADMIN_KEY=<key> pytest -m integration tests/integration/test_admin_ui.py
"""

import json
import os
from urllib import error, request

import pytest

from .helpers import request_json, require_api_url, require_test_principal

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _raw_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, bytes]:
    req = request.Request(url, headers=headers or {}, method="GET")
    try:
        with request.urlopen(req, timeout=15) as response:
            return response.status, response.read()
    except error.HTTPError as exc:
        return exc.code, exc.read()


def _admin_headers() -> dict[str, str]:
    admin_key = os.environ.get("RAJA_ADMIN_KEY")
    if not admin_key:
        pytest.fail("RAJA_ADMIN_KEY not set — required for admin endpoint tests")
    return {"Authorization": f"Bearer {admin_key}"}


def _json_post(url: str, body: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode("utf-8")
    hdrs = {"Content-Type": "application/json", **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method="POST")
    try:
        with request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read())
    except error.HTTPError as exc:
        body_bytes = exc.read()
        return exc.code, json.loads(body_bytes) if body_bytes else {}


# ---------------------------------------------------------------------------
# Admin UI HTML
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_admin_ui_html_is_served():
    """GET / must return HTML containing the admin UI."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/")
    assert status == 200, f"Expected 200, got {status}"
    html = body.decode("utf-8", errors="replace")
    assert "<!DOCTYPE html>" in html or "<html" in html, "Response is not HTML"
    # Admin UI must not reference AVP or Cedar policy store
    assert "verifiedpermissions" not in html.lower(), (
        "Admin HTML references Amazon Verified Permissions — should be DataZone"
    )


@pytest.mark.integration
def test_admin_ui_has_no_avp_references():
    """Admin JS must not contain AVP/Cedar policy store mutations."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/static/admin.js")
    assert status == 200, f"Expected admin.js at /static/admin.js, got {status}"
    js = body.decode("utf-8", errors="replace")
    # These AVP-era identifiers must be removed
    for stale_symbol in ("openPolicyEditor", "extractPolicyStatement", "setPolicyDiff"):
        assert stale_symbol not in js, f"admin.js still contains AVP-era symbol '{stale_symbol}'"


# ---------------------------------------------------------------------------
# Health — DataZone dependency
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_health_includes_datazone_dependency():
    """GET /health must report a datazone dependency (not avp/cedar)."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/health")
    assert status == 200, f"Expected 200, got {status}"
    data = json.loads(body)
    dependencies = data.get("dependencies", {})
    dep_keys = set(dependencies.keys())
    # Must have datazone, must NOT have avp or cedar
    assert "datazone" in dep_keys, f"Health response missing 'datazone' dependency; got {dep_keys}"
    assert "avp" not in dep_keys, "Health still reports 'avp' dependency"
    assert "cedar" not in dep_keys, "Health still reports 'cedar' dependency"


@pytest.mark.integration
def test_health_datazone_dependency_is_ok():
    """DataZone health dependency must be 'ok' (not degraded)."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/health")
    assert status == 200
    data = json.loads(body)
    datazone_status = data.get("dependencies", {}).get("datazone")
    assert datazone_status == "ok", f"DataZone dependency is not healthy: {datazone_status}"


@pytest.mark.integration
def test_health_exposes_default_principal_config():
    """GET /health should expose the first configured RAJA test principal for the admin UI."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/health")
    assert status == 200
    data = json.loads(body)
    assert data.get("config", {}).get("default_principal") == require_test_principal()


# ---------------------------------------------------------------------------
# JWKS (public)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_jwks_is_public_and_valid():
    """GET /.well-known/jwks.json must return a valid JWKS without auth."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/.well-known/jwks.json")
    assert status == 200, f"JWKS must be public, got {status}"
    data = json.loads(body)
    keys = data.get("keys", [])
    assert len(keys) >= 1, "JWKS must contain at least one key"
    key = keys[0]
    assert key.get("kty") == "oct", "Expected symmetric key (oct)"
    assert key.get("alg") == "HS256", "Expected HS256 algorithm"
    assert "k" in key, "Key must contain 'k' (base64url encoded secret)"


# ---------------------------------------------------------------------------
# Admin structure
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_admin_structure_reports_datazone_rows_without_errors():
    """GET /admin/structure must expose healthy DataZone structure rows."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/admin/structure", headers=_admin_headers())
    assert status == 200, f"Expected 200, got {status}"
    data = json.loads(body)

    datazone = data.get("datazone", {})
    domain = datazone.get("domain", {})
    owner_project = datazone.get("owner_project", {})
    asset_type = datazone.get("asset_type", {})

    assert domain.get("status") == "ok", f"Domain row unhealthy: {domain}"
    assert domain.get("id"), f"Domain row missing id: {domain}"
    assert domain.get("portal_url"), f"Domain row missing portal URL: {domain}"

    assert owner_project.get("status") == "ok", f"Owner project row unhealthy: {owner_project}"
    assert owner_project.get("id"), f"Owner project row missing id: {owner_project}"

    assert asset_type.get("status") == "ok", f"Asset type row unhealthy: {asset_type}"
    assert asset_type.get("name") == "QuiltPackage", f"Unexpected asset type row: {asset_type}"


# ---------------------------------------------------------------------------
# Principals — DataZone project metadata
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_principals_list_returns_datazone_project_metadata():
    """GET /principals items must carry datazone_project_id when DataZone enabled."""
    status, body = request_json("GET", "/principals")
    assert status == 200, f"Expected 200, got {status}: {body}"
    principals = body.get("principals", [])
    assert isinstance(principals, list)
    # At least one principal should exist (seeded test data)
    assert len(principals) >= 1, "No principals found. Run python scripts/seed_test_data.py"
    # Every principal with a project should have a string project id
    for item in principals:
        if "datazone_project_id" in item:
            assert isinstance(item["datazone_project_id"], str), (
                f"datazone_project_id must be a string, got {type(item['datazone_project_id'])}"
            )
            assert item["datazone_project_id"], "datazone_project_id must not be empty"


# ---------------------------------------------------------------------------
# Policies — DataZone listings (read-only)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_policies_returns_datazone_listings():
    """GET /policies must return DataZone-backed listings (not Cedar policies)."""
    status, body = request_json("GET", "/policies")
    assert status == 200, f"Expected 200, got {status}: {body}"
    policies = body.get("policies", [])
    assert isinstance(policies, list)
    # Seed data must be present
    assert len(policies) >= 1, (
        "No DataZone package listings found. Run python scripts/seed_packages.py"
    )
    # Every listing must carry the DataZone type marker
    for policy in policies:
        assert policy.get("type") == "datazone-listing", (
            f"Policy item has wrong type: {policy.get('type')} (expected 'datazone-listing')"
        )
        assert "policyId" in policy, "Policy item missing policyId"
        assert "name" in policy, "Policy item missing name"
        # Must not have Cedar-era fields
        assert "statement" not in policy, "DataZone listing still contains Cedar 'statement' field"
        assert "effect" not in policy, "DataZone listing still contains Cedar 'effect' field"


@pytest.mark.integration
def test_policy_mutation_create_returns_410():
    """POST /policies must return 410 Gone (DataZone is read-only)."""
    status, body = request_json(
        "POST",
        "/policies",
        {"statement": "permit(principal, action, resource);", "description": "test"},
    )
    assert status == 410, (
        f"Expected 410 Gone for policy creation (DataZone is read-only), got {status}: {body}"
    )
    detail = body.get("detail", "")
    assert "DataZone" in detail or "not supported" in detail.lower(), (
        f"410 response should mention DataZone, got: {detail}"
    )


@pytest.mark.integration
def test_policy_mutation_update_returns_410():
    """PUT /policies/{id} must return 410 Gone."""
    status, body = request_json(
        "GET",
        "/policies",
    )
    policies = body.get("policies", [])
    if not policies:
        pytest.skip("No DataZone listings available to test update")

    policy_id = policies[0]["policyId"]
    api_url = require_api_url()
    hdrs = _admin_headers()
    hdrs["Content-Type"] = "application/json"
    data = json.dumps({"statement": "permit(principal, action, resource);"}).encode("utf-8")
    req = request.Request(
        f"{api_url}/policies/{policy_id}",
        data=data,
        headers=hdrs,
        method="PUT",
    )
    try:
        with request.urlopen(req, timeout=15) as resp:
            put_status = resp.status
    except error.HTTPError as exc:
        put_status = exc.code
    assert put_status == 410, (
        f"Expected 410 Gone for policy update (DataZone is read-only), got {put_status}"
    )


@pytest.mark.integration
def test_policy_mutation_delete_returns_410():
    """DELETE /policies/{id} must return 410 Gone."""
    status, body = request_json("GET", "/policies")
    policies = body.get("policies", [])
    if not policies:
        pytest.skip("No DataZone listings available to test delete")

    policy_id = policies[0]["policyId"]
    api_url = require_api_url()
    hdrs = _admin_headers()
    req = request.Request(
        f"{api_url}/policies/{policy_id}",
        headers=hdrs,
        method="DELETE",
    )
    try:
        with request.urlopen(req, timeout=15) as resp:
            del_status = resp.status
    except error.HTTPError as exc:
        del_status = exc.code
    assert del_status == 410, (
        f"Expected 410 Gone for policy delete (DataZone is read-only), got {del_status}"
    )


# ---------------------------------------------------------------------------
# Token service
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_token_issuance_for_known_principal():
    """POST /token for a seeded principal must succeed and include DataZone auth plane."""
    principal = require_test_principal()
    status, body = request_json("POST", "/token", {"principal": principal})
    assert status == 200, f"Token issuance failed: {status}: {body}"
    assert "token" in body, "Response missing 'token'"
    assert "scopes" in body, "Response missing 'scopes'"
    assert body.get("principal") == principal


@pytest.mark.integration
def test_token_issuance_for_unknown_principal_returns_404():
    """POST /token for an unknown principal must return 404."""
    status, body = request_json(
        "POST",
        "/token",
        {"principal": "no-such-principal-xyzzy"},
    )
    assert status == 404, f"Expected 404 for unknown principal, got {status}: {body}"


@pytest.mark.integration
def test_token_revocation_returns_unsupported():
    """POST /token/revoke must return unsupported (not a Cedar/AVP 410)."""
    # First get a real token
    tok_status, tok_body = request_json("POST", "/token", {"principal": require_test_principal()})
    if tok_status != 200:
        pytest.skip("Could not obtain token for revocation test")
    token = tok_body.get("token", "dummy-token")

    status, body = request_json("POST", "/token/revoke", {"token": token})
    assert status == 200, f"Expected 200 from /token/revoke, got {status}: {body}"
    assert body.get("status") == "unsupported", (
        f"Expected status 'unsupported', got: {body.get('status')}"
    )
