from fastapi.testclient import TestClient

from raja.server.app import app


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
