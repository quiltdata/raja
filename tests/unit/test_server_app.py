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


# --- Policy CRUD tests ---

_VALID_STATEMENT = (
    'permit(principal == Raja::User::"alice", action == Raja::Action::"read",'
    ' resource == Raja::S3Object::"quilt+s3://b/p@h");'
)

_VALIDATE_PATH = "raja.server.routers.control_plane.validate_policy_against_schema"
_STORE_PATH = "raja.server.routers.control_plane.POLICY_STORE_ID"
_TEST_STORE = "test-store-id"


def test_create_policy_success() -> None:
    mock_avp = MagicMock()
    mock_avp.create_policy.return_value = {"policyId": "p-123"}
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_avp_client] = lambda: mock_avp
    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        with patch(_STORE_PATH, _TEST_STORE), patch(_VALIDATE_PATH):
            client = TestClient(app)
            response = client.post("/policies", json={"statement": _VALID_STATEMENT})
        assert response.status_code == 200
        assert response.json()["policyId"] == "p-123"
        mock_audit.put_item.assert_called_once()
        assert mock_audit.put_item.call_args[1]["Item"]["action"] == "policy.create"
    finally:
        app.dependency_overrides.clear()


def test_create_policy_invalid_cedar() -> None:
    mock_avp = MagicMock()
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_avp_client] = lambda: mock_avp
    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        with (
            patch(_STORE_PATH, _TEST_STORE),
            patch(_VALIDATE_PATH, side_effect=ValueError("unknown resource type: Bad")),
        ):
            client = TestClient(app)
            response = client.post("/policies", json={"statement": "bad cedar"})
        assert response.status_code == 422
        mock_avp.create_policy.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_get_policy_by_id() -> None:
    mock_avp = MagicMock()
    mock_avp.get_policy.return_value = {"definition": {"static": {"statement": _VALID_STATEMENT}}}

    app.dependency_overrides[dependencies.get_avp_client] = lambda: mock_avp
    try:
        with patch(_STORE_PATH, _TEST_STORE):
            client = TestClient(app)
            response = client.get("/policies/p-123")
        assert response.status_code == 200
        payload = response.json()
        assert payload["policyId"] == "p-123"
        assert "definition" in payload
    finally:
        app.dependency_overrides.clear()


def test_update_policy_success() -> None:
    mock_avp = MagicMock()
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_avp_client] = lambda: mock_avp
    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        with patch(_STORE_PATH, _TEST_STORE), patch(_VALIDATE_PATH):
            client = TestClient(app)
            response = client.put("/policies/p-123", json={"statement": _VALID_STATEMENT})
        assert response.status_code == 200
        payload = response.json()
        assert payload["policyId"] == "p-123"
        assert payload["updated"] is True
        mock_avp.update_policy.assert_called_once()
        assert mock_audit.put_item.call_args[1]["Item"]["action"] == "policy.update"
    finally:
        app.dependency_overrides.clear()


def test_update_policy_invalid_cedar() -> None:
    mock_avp = MagicMock()
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_avp_client] = lambda: mock_avp
    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        with (
            patch(_STORE_PATH, _TEST_STORE),
            patch(_VALIDATE_PATH, side_effect=ValueError("unknown action: bad")),
        ):
            client = TestClient(app)
            response = client.put("/policies/p-123", json={"statement": "bad cedar"})
        assert response.status_code == 422
        mock_avp.update_policy.assert_not_called()
    finally:
        app.dependency_overrides.clear()


def test_delete_policy_success() -> None:
    mock_avp = MagicMock()
    mock_audit = MagicMock()

    app.dependency_overrides[dependencies.get_avp_client] = lambda: mock_avp
    app.dependency_overrides[dependencies.get_audit_table] = lambda: mock_audit
    try:
        with patch(_STORE_PATH, _TEST_STORE):
            client = TestClient(app)
            response = client.delete("/policies/p-123")
        assert response.status_code == 200
        payload = response.json()
        assert payload["policyId"] == "p-123"
        assert payload["deleted"] is True
        mock_avp.delete_policy.assert_called_once()
        assert mock_audit.put_item.call_args[1]["Item"]["action"] == "policy.delete"
    finally:
        app.dependency_overrides.clear()
