import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

server_app = importlib.import_module("raja.server.app")
dependencies = importlib.import_module("raja.server.dependencies")

app = server_app.app


@pytest.fixture(autouse=True)
def bypass_admin_auth() -> None:
    app.dependency_overrides[dependencies.require_admin_auth] = lambda: None
    yield
    app.dependency_overrides.clear()


def test_admin_home_returns_html():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "RAJA Admin" in response.text


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert "status" in payload
    assert "dependencies" in payload


def test_audit_endpoint_returns_entries() -> None:
    mock_table = MagicMock()
    mock_table.query.return_value = {
        "Items": [
            {
                "pk": "AUDIT",
                "event_id": "1",
                "timestamp": 1234567890,
                "principal": "alice",
                "action": "token.issue",
                "resource": "alice",
                "decision": "SUCCESS",
                "policy_store_id": "store",
                "request_id": "req",
            }
        ]
    }

    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_table
    try:
        client = TestClient(app)
        response = client.get("/audit")
        assert response.status_code == 200
        payload = response.json()
        assert payload["entries"]
        assert payload["entries"][0]["principal"] == "alice"
    finally:
        app.dependency_overrides.clear()


# --- Listing surface tests ---

_VALID_STATEMENT = (
    'permit(principal == Raja::User::"alice", action == Raja::Action::"read",'
    ' resource == Raja::S3Object::"quilt+s3://b/p@h");'
)


def test_create_policy_returns_gone() -> None:
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        client = TestClient(app)
        response = client.post("/policies", json={"statement": _VALID_STATEMENT})
        assert response.status_code == 410
        mock_audit.put_item.assert_called_once()
        assert mock_audit.put_item.call_args[1]["Item"]["action"] == "policy.create"
    finally:
        app.dependency_overrides.clear()


def test_get_policy_by_id() -> None:
    mock_datazone = MagicMock()
    response_payload = [
        {
            "listingId": "l-123",
            "name": "demo/package-grant",
            "entityType": "QuiltPackage",
            "owningProjectId": "proj-owner",
        }
    ]

    app.dependency_overrides[dependencies.get_datazone_client] = lambda: mock_datazone
    try:
        with patch("raja.server.routers.control_plane._datazone_service") as factory:
            service = factory.return_value
            service._config.asset_type_name = "QuiltPackage"
            service._search_listings.return_value = response_payload
            client = TestClient(app)
            response = client.get("/policies/l-123")
        assert response.status_code == 200
        payload = response.json()
        assert payload["policyId"] == "l-123"
        assert "definition" in payload
    finally:
        app.dependency_overrides.clear()


def test_update_policy_returns_gone() -> None:
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        client = TestClient(app)
        response = client.put("/policies/p-123", json={"statement": _VALID_STATEMENT})
        assert response.status_code == 410
        assert mock_audit.put_item.call_args[1]["Item"]["action"] == "policy.update"
    finally:
        app.dependency_overrides.clear()


def test_delete_policy_returns_gone() -> None:
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        client = TestClient(app)
        response = client.delete("/policies/p-123")
        assert response.status_code == 410
        assert mock_audit.put_item.call_args[1]["Item"]["action"] == "policy.delete"
    finally:
        app.dependency_overrides.clear()


def test_list_policies_returns_datazone_listings() -> None:
    mock_datazone = MagicMock()
    response_payload = [
        {
            "listingId": "l-123",
            "name": "demo/package-grant",
            "entityType": "QuiltPackage",
            "owningProjectId": "proj-owner",
        }
    ]

    app.dependency_overrides[dependencies.get_datazone_client] = lambda: mock_datazone
    try:
        with patch("raja.server.routers.control_plane._datazone_service") as factory:
            service = factory.return_value
            service._config.asset_type_name = "QuiltPackage"
            service._search_listings.return_value = response_payload
            client = TestClient(app)
            response = client.get("/policies")
        assert response.status_code == 200
        payload = response.json()
        assert payload["policies"][0]["policyId"] == "l-123"
    finally:
        app.dependency_overrides.clear()


# --- Static asset path tests (regression: API Gateway stage prefix) ---
#
# When deployed behind an API Gateway stage (e.g. /prod), absolute paths like
# /static/admin.css resolve to the root of the host, bypassing the stage
# prefix, and return 403.  All static asset references in admin.html must be
# relative so they resolve relative to whatever stage the page is served from.


def test_admin_html_static_refs_are_relative():
    """admin.html must use relative paths for static assets.

    Absolute paths (starting with '/') break under API Gateway stage prefixes —
    the request goes to the host root instead of /stage/static/..., returning 403.
    """
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200

    html = response.text
    # Collect every href/src value that references /static/
    import re

    absolute_refs = re.findall(r'(?:href|src)="(/static/[^"]+)"', html)
    assert not absolute_refs, (
        f"admin.html has absolute static paths (breaks under API Gateway stages): {absolute_refs}"
    )


def test_static_assets_are_served():
    """Static assets must return 200 so the admin UI can load."""
    client = TestClient(app)
    for path in ("/static/admin.css", "/static/admin.js"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"
