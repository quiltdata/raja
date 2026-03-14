import importlib
import re
from unittest.mock import patch

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


def test_create_policy_returns_gone() -> None:
    client = TestClient(app)
    response = client.post(
        "/policies",
        json={"statement": "permit(principal, action, resource);"},
    )
    assert response.status_code == 410


def test_get_policy_by_id() -> None:
    from unittest.mock import MagicMock

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
    client = TestClient(app)
    response = client.put(
        "/policies/p-123", json={"statement": "permit(principal, action, resource);"}
    )
    assert response.status_code == 410


def test_delete_policy_returns_gone() -> None:
    client = TestClient(app)
    response = client.delete("/policies/p-123")
    assert response.status_code == 410


def test_list_policies_returns_datazone_listings() -> None:
    from unittest.mock import MagicMock

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


def test_admin_html_static_refs_are_relative():
    """admin.html must use relative paths for static assets."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    html = response.text
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
