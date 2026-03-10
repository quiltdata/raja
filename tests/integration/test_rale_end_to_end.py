from __future__ import annotations

import json
import time
import uuid
from urllib.parse import quote

import boto3
import jwt
import pytest

from .helpers import (
    fetch_jwks_secret,
    request_url,
    require_manifest_cache_table,
    require_rajee_endpoint,
    require_rajee_test_bucket,
    require_rale_router_url,
    require_taj_cache_table,
)


def _request_router_with_retry(
    url: str, taj: str, attempts: int = 3
) -> tuple[int, dict[str, str], bytes]:
    """Retry transient Envoy upstream resets on first-hop router requests."""
    last: tuple[int, dict[str, str], bytes] | None = None
    for _ in range(attempts):
        status, headers, body = request_url("GET", url, headers={"x-rale-taj": taj})
        last = (status, headers, body)
        text = body.decode("utf-8", errors="replace")
        if status != 503 or "connection termination" not in text:
            return last
        time.sleep(1)
    assert last is not None
    return last


@pytest.mark.integration
def test_rale_envoy_authorizer_then_router_roundtrip() -> None:
    """Verify Envoy routes to RALE authorizer (no TAJ) and RALE router (with TAJ)."""
    principal = "test-user"
    registry = "registry"
    package_name = "demo/package"
    manifest_hash = uuid.uuid4().hex
    logical_key = f"file-{uuid.uuid4().hex}.txt"

    usl_path = f"/{registry}/{package_name}@{manifest_hash}/{logical_key}"
    encoded_usl_path = quote(usl_path, safe="/@")

    jwt_secret = fetch_jwks_secret()

    taj = jwt.encode(
        {
            "sub": principal,
            "grants": [f"s3:GetObject/{registry}/{package_name}@{manifest_hash}/"],
            "manifest_hash": manifest_hash,
            "package_name": package_name,
            "registry": registry,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        jwt_secret,
        algorithm="HS256",
    )

    taj_cache_table = boto3.resource("dynamodb").Table(require_taj_cache_table())
    taj_cache_table.put_item(
        Item={
            "cache_key": f"{principal}#{manifest_hash}",
            "taj": taj,
            "decision": "ALLOW",
            "ttl": int(time.time()) + 300,
        }
    )

    bucket = require_rajee_test_bucket()
    source_key = f"rale-integration/{uuid.uuid4().hex}.txt"
    expected_body = b"rale-envoy-router-object"
    boto3.client("s3").put_object(Bucket=bucket, Key=source_key, Body=expected_body)

    manifest_cache = boto3.resource("dynamodb").Table(require_manifest_cache_table())
    manifest_cache.put_item(
        Item={
            "manifest_hash": manifest_hash,
            "entries": {
                logical_key: [
                    {
                        "bucket": bucket,
                        "key": source_key,
                    }
                ]
            },
        }
    )

    rajee_endpoint = require_rajee_endpoint()

    status, _, body = request_url(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    authorizer_payload = json.loads(body.decode("utf-8"))
    assert authorizer_payload.get("token") == taj
    assert authorizer_payload.get("cached") is True

    status, _, body = _request_router_with_retry(f"{rajee_endpoint}{encoded_usl_path}", taj)
    assert status == 200, body.decode("utf-8", errors="replace")
    assert body == expected_body


@pytest.mark.integration
def test_rale_router_direct_invocation_with_manifest_membership() -> None:
    """Verify router lambda enforces manifest membership and returns object content."""
    principal = "test-user"
    registry = "registry"
    package_name = "demo/package"
    manifest_hash = uuid.uuid4().hex
    logical_key = f"member-{uuid.uuid4().hex}.txt"
    usl_path = f"/{registry}/{package_name}@{manifest_hash}/{logical_key}"
    encoded_usl_path = quote(usl_path, safe="/@")

    jwt_secret = fetch_jwks_secret()

    taj = jwt.encode(
        {
            "sub": principal,
            "grants": [f"s3:GetObject/{registry}/{package_name}@{manifest_hash}/"],
            "manifest_hash": manifest_hash,
            "package_name": package_name,
            "registry": registry,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        jwt_secret,
        algorithm="HS256",
    )

    bucket = require_rajee_test_bucket()
    source_key = f"rale-integration/{uuid.uuid4().hex}.txt"
    expected_body = b"rale-router-direct"
    boto3.client("s3").put_object(Bucket=bucket, Key=source_key, Body=expected_body)

    manifest_cache = boto3.resource("dynamodb").Table(require_manifest_cache_table())
    manifest_cache.put_item(
        Item={
            "manifest_hash": manifest_hash,
            "entries": {
                logical_key: [
                    {
                        "bucket": bucket,
                        "key": source_key,
                    }
                ]
            },
        }
    )

    router_url = require_rale_router_url()
    status, _, body = request_url(
        "GET",
        f"{router_url}{encoded_usl_path}",
        headers={"x-rale-taj": taj},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    assert body == expected_body


@pytest.mark.integration
def test_rale_router_cache_miss_exercises_manifest_resolution_path() -> None:
    """Ensure cache-miss path executes resolver code and not a missing quilt3 dependency."""
    principal = "test-user"
    registry = f"registry-{uuid.uuid4().hex}"
    package_name = "demo/package"
    manifest_hash = uuid.uuid4().hex
    logical_key = f"missing-{uuid.uuid4().hex}.txt"
    usl_path = f"/{registry}/{package_name}@{manifest_hash}/{logical_key}"
    encoded_usl_path = quote(usl_path, safe="/@")

    jwt_secret = fetch_jwks_secret()

    taj = jwt.encode(
        {
            "sub": principal,
            "grants": [f"s3:GetObject/{registry}/{package_name}@{manifest_hash}/"],
            "manifest_hash": manifest_hash,
            "package_name": package_name,
            "registry": registry,
            "iat": int(time.time()),
            "exp": int(time.time()) + 3600,
        },
        jwt_secret,
        algorithm="HS256",
    )

    # Ensure the manifest is absent so router has to execute resolve_package_map().
    manifest_cache = boto3.resource("dynamodb").Table(require_manifest_cache_table())
    manifest_cache.delete_item(Key={"manifest_hash": manifest_hash})

    router_url = require_rale_router_url()
    status, _, body = request_url(
        "GET",
        f"{router_url}{encoded_usl_path}",
        headers={"x-rale-taj": taj},
    )

    # Unknown fake package can fail resolution, but should not fail due to missing dependency.
    assert status in (502, 403, 404), body.decode("utf-8", errors="replace")
    text = body.decode("utf-8", errors="replace")
    assert "quilt3 is required for package resolution" not in text


@pytest.mark.integration
def test_rale_complete_flow_end_to_end() -> None:
    """
    COMPREHENSIVE RALE SYSTEM TEST

    Validates the entire stack in a single test with step-by-step output:

      Control Plane              RALE Stack                  AWS
      ─────────────              ──────────                  ───
      /health ✓                  Authorizer (cache hit) ─►  TAJ
      /.well-known/jwks.json ◄── Router validates TAJ    ─► S3
      POST /token ✓

    Steps:
      1. Control plane health, JWKS, and token issuance
      2. S3 object upload + manifest cache seed
      3. TAJ creation (signed with control plane JWKS secret) + cache seed
      4. RALE authorizer returns cached TAJ
      5. Verify TAJ validates against JWKS key
      6. RALE router returns S3 object content
    """
    import base64
    import json as _json

    principal = "test-user"
    registry = "registry"
    package_name = "demo/e2e"
    manifest_hash = uuid.uuid4().hex
    logical_key = f"data/e2e-{uuid.uuid4().hex}.txt"
    expected_body = b"rale-complete-flow-system-test"

    # Un-pinned USL — no @hash; the authorizer will resolve and pin it.
    unpinned_usl_path = f"/{registry}/{package_name}/{logical_key}"
    encoded_usl_path = quote(unpinned_usl_path, safe="/@")

    print("\n" + "=" * 72)
    print("RALE SYSTEM TEST: Control Plane → Authorizer → Router → S3")
    print("=" * 72)
    print(f"\n  Logical USL (un-pinned): s3+rale:/{unpinned_usl_path}")
    print(f"  Will pin to hash:        {manifest_hash}")
    print(f"  logical_key:             {logical_key}")

    # ------------------------------------------------------------------
    # 1. Control plane
    # ------------------------------------------------------------------
    print("\n[CONTROL PLANE]")

    from .helpers import issue_token, request_json

    status, _ = request_json("GET", "/health")
    assert status == 200
    print("  /health                    OK")

    status, jwks_body = request_json("GET", "/.well-known/jwks.json")
    assert status == 200
    keys = jwks_body.get("keys", [])
    assert keys, "JWKS has no keys"
    jwks_key_b64 = keys[0]["k"]
    padding = "=" * (-len(jwks_key_b64) % 4)
    jwt_secret = base64.urlsafe_b64decode(jwks_key_b64 + padding).decode("utf-8")
    print(f"  /.well-known/jwks.json     {len(keys)} key(s)")

    token, scopes = issue_token(principal)
    assert token and scopes
    print(f"  POST /token ({principal})  {len(scopes)} scope(s)")

    # ------------------------------------------------------------------
    # 2. S3 setup
    # ------------------------------------------------------------------
    print("\n[S3 SETUP]")
    bucket = require_rajee_test_bucket()
    source_key = f"rale-e2e/{uuid.uuid4().hex}.txt"
    boto3.client("s3").put_object(Bucket=bucket, Key=source_key, Body=expected_body)
    print(f"  Physical S3:    s3://{bucket}/{source_key}")
    print(f"  Object data:    {expected_body!r}")

    manifest_cache = boto3.resource("dynamodb").Table(require_manifest_cache_table())
    # Seed the physical manifest entries (hash → entries).
    manifest_cache.put_item(
        Item={
            "manifest_hash": manifest_hash,
            "entries": {logical_key: [{"bucket": bucket, "key": source_key}]},
        }
    )
    # Seed the "latest" pointer so the authorizer can pin the un-pinned USL.
    pointer_key = f"pkg:{registry}/{package_name}"
    manifest_cache.put_item(Item={"manifest_hash": pointer_key, "latest_hash": manifest_hash})
    print(f"  Manifest pin:   {logical_key}")
    print(f"    → s3://{bucket}/{source_key}")
    print(f"  Latest pointer: {pointer_key} → {manifest_hash}")

    # ------------------------------------------------------------------
    # 3. Build TAJ and seed the authorizer cache
    # ------------------------------------------------------------------
    print("\n[TAJ]")
    now = int(time.time())
    taj_claims = {
        "sub": principal,
        "grants": [f"s3:GetObject/{registry}/{package_name}@{manifest_hash}/"],
        "manifest_hash": manifest_hash,
        "package_name": package_name,
        "registry": registry,
        "iat": now,
        "exp": now + 3600,
    }
    taj = jwt.encode(taj_claims, jwt_secret, algorithm="HS256")
    taj_cache = boto3.resource("dynamodb").Table(require_taj_cache_table())
    taj_cache.put_item(
        Item={
            "cache_key": f"{principal}#{manifest_hash}",
            "taj": taj,
            "decision": "ALLOW",
            "ttl": now + 300,
        }
    )
    print(f"  sub:       {taj_claims['sub']}")
    print(f"  grants:    {taj_claims['grants']}")
    print(f"  cache_key: {principal}#{manifest_hash}  ← pinned by authorizer")
    print(f"  TAJ (JWT): {taj[:60]}…")

    # ------------------------------------------------------------------
    # 4. RALE authorizer
    # ------------------------------------------------------------------
    print("\n[RALE AUTHORIZER]")
    rajee_endpoint = require_rajee_endpoint()
    status, _, body = request_url(
        "GET",
        f"{rajee_endpoint}{encoded_usl_path}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    auth_payload = _json.loads(body.decode("utf-8"))
    returned_taj = auth_payload.get("token")
    assert returned_taj == taj, "Authorizer returned unexpected TAJ"
    cached = auth_payload.get("cached")
    decision = auth_payload.get("decision")
    pinned_hash = auth_payload.get("manifest_hash")
    print(f"  Received un-pinned USL, resolved manifest_hash={pinned_hash}")
    print(f"  TAJ returned (cached={cached}, decision={decision})")

    # ------------------------------------------------------------------
    # 5. Verify TAJ validates against the JWKS key
    # ------------------------------------------------------------------
    decoded = jwt.decode(returned_taj, jwt_secret, algorithms=["HS256"])
    assert decoded["sub"] == principal
    print("  TAJ validates against control plane JWKS key ✓")

    # ------------------------------------------------------------------
    # 6. RALE router
    # ------------------------------------------------------------------
    print("\n[RALE ROUTER]")
    print(f"  boto3 reconfiguration:  endpoint_url={rajee_endpoint!r}")
    print(
        f"  Client sends un-pinned: s3.get_object(Bucket='{registry}',"
        f" Key='{package_name}/{logical_key}')"
    )
    print(f"  Router pins via TAJ:    manifest_hash={manifest_hash}")
    status, _, retrieved_body = _request_router_with_retry(
        f"{rajee_endpoint}{encoded_usl_path}", returned_taj
    )
    assert status == 200, retrieved_body.decode("utf-8", errors="replace")
    assert retrieved_body == expected_body
    print(f"  Retrieved ({len(retrieved_body)} bytes): {retrieved_body!r}")

    print("\n" + "=" * 72)
    print("SYSTEM TEST PASSED")
    print("  Control plane:    health OK, JWKS reachable, token issued")
    print("  RALE authorizer:  TAJ served (cached), validates against JWKS key")
    print("  RALE router:      TAJ validated, manifest resolved, S3 object returned")
    print("=" * 72)
