import importlib.util
import os
import sys
from pathlib import Path

from fastapi.testclient import TestClient


def load_authorizer_module():
    root = Path(__file__).resolve().parents[2]
    module_path = root / "lambda_handlers" / "authorizer" / "app.py"
    spec = importlib.util.spec_from_file_location("raja_authorizer_app", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load authorizer module")
    module = importlib.util.module_from_spec(spec)
    sys.modules["raja_authorizer_app"] = module
    spec.loader.exec_module(module)
    return module


class _DummyCloudWatchClient:
    def put_metric_data(self, **kwargs) -> None:
        return None


def test_authorize_allows_when_checks_disabled(monkeypatch) -> None:
    old_disable = os.environ.get("DISABLE_AUTH_CHECKS")
    old_secret = os.environ.get("JWT_SECRET")
    os.environ["DISABLE_AUTH_CHECKS"] = "true"
    os.environ.pop("JWT_SECRET", None)
    try:
        module = load_authorizer_module()
        monkeypatch.setattr(module, "get_cloudwatch_client", lambda: _DummyCloudWatchClient())
        client = TestClient(module.app)
        response = client.post(
            "/authorize",
            json={
                "attributes": {
                    "request": {
                        "http": {
                            "method": "GET",
                            "path": "/demo-bucket/object.txt",
                            "headers": {},
                            "query_params": {},
                        }
                    }
                }
            },
        )
        assert response.status_code == 200
        assert response.json()["result"]["allowed"] is True
    finally:
        if old_disable is None:
            os.environ.pop("DISABLE_AUTH_CHECKS", None)
        else:
            os.environ["DISABLE_AUTH_CHECKS"] = old_disable
        if old_secret is None:
            os.environ.pop("JWT_SECRET", None)
        else:
            os.environ["JWT_SECRET"] = old_secret


def test_readiness_allows_when_checks_disabled() -> None:
    old_disable = os.environ.get("DISABLE_AUTH_CHECKS")
    old_secret = os.environ.get("JWT_SECRET")
    os.environ["DISABLE_AUTH_CHECKS"] = "true"
    os.environ.pop("JWT_SECRET", None)
    try:
        module = load_authorizer_module()
        client = TestClient(module.app)
        response = client.get("/ready")
        assert response.status_code == 200
        assert response.json()["status"] == "ready"
    finally:
        if old_disable is None:
            os.environ.pop("DISABLE_AUTH_CHECKS", None)
        else:
            os.environ["DISABLE_AUTH_CHECKS"] = old_disable
        if old_secret is None:
            os.environ.pop("JWT_SECRET", None)
        else:
            os.environ["JWT_SECRET"] = old_secret
