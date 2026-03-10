"""Integration tests for health endpoint reachability and admin auth.

These tests would have caught the "stuck on checking..." UI bug:
- /health must respond quickly with status "ok"
- Protected endpoints must reject requests without a valid admin key
- Protected endpoints must accept requests with the correct admin key
"""

import json
import os
from urllib import error, request

import pytest

from .helpers import require_api_url


def _raw_get(url: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    req = request.Request(url, headers=headers or {}, method="GET")
    try:
        with request.urlopen(req, timeout=15) as response:
            return response.status, json.loads(response.read())
    except error.HTTPError as exc:
        body = exc.read()
        return exc.code, json.loads(body) if body else {}


@pytest.mark.integration
def test_health_is_reachable_and_ok():
    """GET /health must respond (not hang) and report status ok.

    This is the server-side equivalent of the loadHealth() UI call.
    A timeout or non-200 here means the chip would freeze at "checking...".
    """
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/health")
    assert status == 200, f"Expected 200, got {status}: {body}"
    assert body.get("status") == "ok", f"Health degraded: {body}"


@pytest.mark.integration
def test_health_is_public_no_key_needed():
    """GET /health must return 200 even without an admin key."""
    api_url = require_api_url()
    # Explicitly no Authorization header
    status, body = _raw_get(f"{api_url}/health")
    assert status == 200, f"Health should be public, got {status}: {body}"


@pytest.mark.integration
def test_protected_endpoint_rejects_missing_key():
    """GET /principals without Authorization header must return 401."""
    api_url = require_api_url()
    status, body = _raw_get(f"{api_url}/principals")
    assert status == 401, f"Expected 401 without key, got {status}: {body}"


@pytest.mark.integration
def test_protected_endpoint_rejects_wrong_key():
    """GET /principals with wrong key must return 401."""
    api_url = require_api_url()
    status, body = _raw_get(
        f"{api_url}/principals",
        headers={"Authorization": "Bearer definitely-wrong-key"},
    )
    assert status == 401, f"Expected 401 with wrong key, got {status}: {body}"


@pytest.mark.integration
def test_protected_endpoint_accepts_correct_key():
    """GET /principals with correct admin key must return 200."""
    api_url = require_api_url()
    admin_key = os.environ.get("RAJA_ADMIN_KEY")
    if not admin_key:
        pytest.skip("RAJA_ADMIN_KEY not set")
    status, body = _raw_get(
        f"{api_url}/principals",
        headers={"Authorization": f"Bearer {admin_key}"},
    )
    assert status == 200, f"Expected 200 with correct key, got {status}: {body}"
    assert "principals" in body
