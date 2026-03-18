from __future__ import annotations

import json
import time
from urllib.parse import quote

import jwt
import pytest

from scripts.seed_packages import seed_files_for_package

from .helpers import (
    fetch_jwks_secret,
    parse_rale_test_quilt_uri,
    request_json,
    request_url,
    require_rajee_endpoint,
    require_rale_router_url,
    require_rale_test_quilt_uri,
    require_test_principal,
)


def _request_with_envoy_retry(
    method: str, url: str, headers: dict[str, str], attempts: int = 3
) -> tuple[int, dict[str, str], bytes]:
    last: tuple[int, dict[str, str], bytes] | None = None
    for _ in range(attempts):
        status, response_headers, body = request_url(method, url, headers=headers)
        last = (status, response_headers, body)
        text = body.decode("utf-8", errors="replace")
        if status != 503 or "connection termination" not in text:
            return last
        time.sleep(1)
    assert last is not None
    return last


@pytest.mark.integration
def test_rale_authorizer_mints_taj_for_real_package() -> None:
    """Authorizer resolves latest hash via quilt3 and mints a real TAJ."""
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)

    principal = require_test_principal()
    usl_path = f"/{parts['registry']}/{parts['package_name']}/data.csv"
    encoded_usl_path = quote(usl_path, safe="/@")

    rajee_endpoint = require_rajee_endpoint()
    status, _, body = _request_with_envoy_retry(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode("utf-8", errors="replace")

    payload = json.loads(body)
    assert payload["cached"] is False
    assert payload["manifest_hash"] == parts["hash"]


@pytest.mark.integration
def test_rale_router_fetches_real_s3_object() -> None:
    """Router resolves package map via quilt3 and returns exact seeded bytes."""
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)

    principal = require_test_principal()
    jwt_secret = fetch_jwks_secret()
    manifest_hash = parts["hash"]
    package_name = parts["package_name"]
    registry = parts["registry"]

    now = int(time.time())
    taj = jwt.encode(
        {
            "sub": principal,
            "grants": [f"s3:GetObject/{registry}/{package_name}@{manifest_hash}/"],
            "manifest_hash": manifest_hash,
            "package_name": package_name,
            "registry": registry,
            "iat": now,
            "exp": now + 3600,
        },
        jwt_secret,
        algorithm="HS256",
    )

    usl_path = f"/{registry}/{package_name}@{manifest_hash}/data.csv"
    encoded_usl_path = quote(usl_path, safe="/@")

    router_url = require_rale_router_url()
    status, _, body = request_url(
        "GET",
        f"{router_url}{encoded_usl_path}",
        headers={"x-rale-taj": taj},
        sigv4=True,
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    assert body == seed_files_for_package(parts["package_name"])["data.csv"]


@pytest.mark.integration
def test_rale_complete_flow_no_preseeding() -> None:
    """Full roundtrip without pre-seeding: authorizer -> router -> exact seeded bytes."""
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)
    principal = require_test_principal()
    logical_key = "README.md"

    usl_path = f"/{parts['registry']}/{parts['package_name']}/{logical_key}"
    encoded_usl_path = quote(usl_path, safe="/@")

    status, _ = request_json("GET", "/health")
    assert status == 200

    rajee_endpoint = require_rajee_endpoint()
    status, _, body = _request_with_envoy_retry(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    payload = json.loads(body)
    taj = payload["token"]
    assert payload["cached"] is False
    assert payload["manifest_hash"] == parts["hash"]

    status, _, body = _request_with_envoy_retry(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-rale-taj": taj},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    assert body == seed_files_for_package(parts["package_name"])["README.md"]
