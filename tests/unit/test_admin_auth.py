from __future__ import annotations

import importlib
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

server_app = importlib.import_module("raja.server.app")
dependencies = importlib.import_module("raja.server.dependencies")

app = server_app.app


def _request_with_headers(headers: list[tuple[bytes, bytes]]) -> Request:
    return Request({"type": "http", "headers": headers})


@pytest.fixture(autouse=True)
def clear_overrides() -> None:
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


def test_valid_key_passes() -> None:
    request = _request_with_headers([(b"authorization", b"Bearer correct-key")])
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "correct-key"}, clear=False):
        assert dependencies.require_admin_auth(request) is None


def test_wrong_key_rejected() -> None:
    request = _request_with_headers([(b"authorization", b"Bearer wrong-key")])
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "correct-key"}, clear=False):
        with pytest.raises(HTTPException) as exc_info:
            dependencies.require_admin_auth(request)
    assert exc_info.value.status_code == 401


def test_missing_header_rejected() -> None:
    request = _request_with_headers([])
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "correct-key"}, clear=False):
        with pytest.raises(HTTPException) as exc_info:
            dependencies.require_admin_auth(request)
    assert exc_info.value.status_code == 401


def test_unset_admin_key_returns_500() -> None:
    request = _request_with_headers([(b"authorization", b"Bearer any-key")])
    with patch.dict("os.environ", {}, clear=True):
        with pytest.raises(HTTPException) as exc_info:
            dependencies.require_admin_auth(request)
    assert exc_info.value.status_code == 500


def test_protected_principals_without_key_returns_401() -> None:
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "admin-key"}, clear=False):
        client = TestClient(app)
        response = client.get("/principals")
    assert response.status_code == 401


def test_protected_principals_wrong_key_returns_401() -> None:
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "admin-key"}, clear=False):
        client = TestClient(app)
        response = client.get("/principals", headers={"Authorization": "Bearer wrong"})
    assert response.status_code == 401


def test_protected_principals_correct_key_returns_200() -> None:
    from unittest.mock import patch as mpatch

    with mpatch("raja.server.routers.control_plane._datazone_service") as factory:
        with mpatch("raja.server.routers.control_plane.DataZoneConfig") as mock_cfg_cls:
            config = MagicMock()
            config.owner_project_id = "proj-alpha"
            config.users_project_id = ""
            config.guests_project_id = ""
            config.owner_project_label = "Alpha"
            config.users_project_label = "Bio"
            config.guests_project_label = "Compute"
            mock_cfg_cls.from_env.return_value = config
            service = factory.return_value
            service.list_project_members.return_value = []
            app.dependency_overrides[dependencies.get_datazone_client] = lambda: MagicMock()

            with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "admin-key"}, clear=False):
                client = TestClient(app)
                response = client.get("/principals", headers={"Authorization": "Bearer admin-key"})

    assert response.status_code == 200


def test_protected_probe_without_key_returns_401() -> None:
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "admin-key"}, clear=False):
        client = TestClient(app)
        response = client.post(
            "/probe/rajee",
            json={
                "principal": "User::alice",
                "usl": "quilt+s3://registry#package=demo/pkg@abc123",
                "rajee_endpoint": "http://localhost:10000",
            },
        )
    assert response.status_code == 401


def test_protected_failure_test_run_without_key_returns_401() -> None:
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "admin-key"}, clear=False):
        client = TestClient(app)
        response = client.post("/api/failure-tests/1.1/run")
    assert response.status_code == 401


def test_protected_rotate_secret_without_key_returns_401() -> None:
    with patch.dict("os.environ", {"RAJA_ADMIN_KEY": "admin-key"}, clear=False):
        client = TestClient(app)
        response = client.post("/admin/rotate-secret")
    assert response.status_code == 401


def test_public_health_without_key_returns_200() -> None:
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200


def test_public_jwks_without_key_returns_200() -> None:
    app.dependency_overrides[dependencies.get_jwt_secret] = lambda: "test-secret"
    client = TestClient(app)
    response = client.get("/.well-known/jwks.json")
    assert response.status_code == 200
