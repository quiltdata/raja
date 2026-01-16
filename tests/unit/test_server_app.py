import importlib
import os
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

server_app = importlib.import_module("raja.server.app")
dependencies = importlib.import_module("raja.server.dependencies")

app = server_app.app


def test_admin_home_returns_html():
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "RAJA Admin" in response.text


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


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


def test_s3_harness_flow_allows_and_denies() -> None:
    os.environ["RAJ_HARNESS_SECRET"] = "test-secret"
    os.environ["RAJ_HARNESS_ISSUER"] = "https://issuer.test"
    os.environ["RAJ_HARNESS_AUDIENCE"] = "s3-aud"
    server_app._harness_secret_cache = None

    client = TestClient(app)
    mint_response = client.post(
        "/s3-harness/mint",
        json={
            "subject": "User::alice",
            "audience": "s3-aud",
            "action": "s3:GetObject",
            "bucket": "demo-bucket",
            "key": "photos/2024/cat.jpg",
            "ttl": 300,
        },
    )
    assert mint_response.status_code == 200
    token = mint_response.json()["token"]

    verify_response = client.post(
        "/s3-harness/verify",
        json={"token": token, "audience": "s3-aud"},
    )
    assert verify_response.status_code == 200
    assert verify_response.json()["valid"] is True

    allow_response = client.post(
        "/s3-harness/enforce",
        json={
            "token": token,
            "audience": "s3-aud",
            "bucket": "demo-bucket",
            "key": "photos/2024/cat.jpg",
            "action": "s3:GetObject",
        },
    )
    assert allow_response.status_code == 200
    allow_payload = allow_response.json()
    assert allow_payload["allowed"] is True

    deny_response = client.post(
        "/s3-harness/enforce",
        json={
            "token": token,
            "audience": "s3-aud",
            "bucket": "demo-bucket",
            "key": "photos/2024/cat.jpg",
            "action": "s3:PutObject",
        },
    )
    assert deny_response.status_code == 200
    deny_payload = deny_response.json()
    assert deny_payload["allowed"] is False
    assert deny_payload["failed_check"] == "action"
