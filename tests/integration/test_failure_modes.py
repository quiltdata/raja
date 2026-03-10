import os
import shutil
import time
import uuid
from typing import Any

import boto3
import jwt as pyjwt
import pytest
from botocore.exceptions import ClientError

from raja.compiler import _expand_templates, compile_policy

from ..shared.s3_client import create_rajee_s3_client
from ..shared.token_builder import TokenBuilder
from .helpers import (
    fetch_jwks_secret,
    issue_rajee_token,
    request_json,
    request_url,
    require_api_issuer,
    require_manifest_cache_table,
    require_rajee_test_bucket,
    require_rale_router_url,
)


def _cedar_tool_available() -> bool:
    return bool(shutil.which("cargo")) or bool(os.environ.get("CEDAR_PARSE_BIN"))


def _list_bucket_status(token: str | None) -> int:
    s3, bucket = create_rajee_s3_client(token=token)
    try:
        s3.list_objects_v2(Bucket=bucket, Prefix="rajee-integration/", MaxKeys=1)
        return 200
    except ClientError as exc:
        return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)


def _make_taj(jwt_secret: str, **overrides: Any) -> str:
    """Build a TAJ using the real JWT secret, with any field overridable."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": "test-user",
        "grants": ["s3:GetObject/registry/demo/package@abc123/"],
        "manifest_hash": "abc123",
        "package_name": "demo/package",
        "registry": "registry",
        "iat": now,
        "exp": now + 3600,
    }
    payload.update(overrides)
    return pyjwt.encode(payload, jwt_secret, algorithm="HS256")


# ---------------------------------------------------------------------------
# Envoy JWT validation (apply in all routing modes)
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_envoy_rejects_expired_token() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    bucket = require_rajee_test_bucket()
    token = (
        TokenBuilder(secret=secret, issuer=issuer, audience="raja-s3-proxy")
        .with_subject("test-user")
        .with_scopes([f"S3Bucket:{bucket}:s3:ListBucket"])
        .with_expiration_in_past(seconds_ago=60)
        .build()
    )
    status = _list_bucket_status(token)
    assert status in (400, 401)


@pytest.mark.integration
def test_envoy_rejects_invalid_signature() -> None:
    issuer = require_api_issuer()
    bucket = require_rajee_test_bucket()
    token = (
        TokenBuilder(secret="wrong-secret", issuer=issuer, audience="raja-s3-proxy")
        .with_subject("test-user")
        .with_scopes([f"S3Bucket:{bucket}:s3:ListBucket"])
        .build()
    )
    assert _list_bucket_status(token) == 401


@pytest.mark.integration
def test_envoy_rejects_wrong_issuer() -> None:
    secret = fetch_jwks_secret()
    bucket = require_rajee_test_bucket()
    token = (
        TokenBuilder(
            secret=secret, issuer="https://wrong-issuer.example.com", audience="raja-s3-proxy"
        )
        .with_subject("test-user")
        .with_scopes([f"S3Bucket:{bucket}:s3:ListBucket"])
        .build()
    )
    assert _list_bucket_status(token) == 401


@pytest.mark.integration
def test_envoy_rejects_wrong_audience() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    bucket = require_rajee_test_bucket()
    token = (
        TokenBuilder(secret=secret, issuer=issuer, audience="wrong-audience")
        .with_subject("test-user")
        .with_scopes([f"S3Bucket:{bucket}:s3:ListBucket"])
        .build()
    )
    assert _list_bucket_status(token) == 403


# ---------------------------------------------------------------------------
# RALE router TAJ validation
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rale_router_rejects_expired_taj() -> None:
    """RALE router returns 401 for a TAJ that has expired."""
    jwt_secret = fetch_jwks_secret()
    now = int(time.time())
    expired_taj = _make_taj(jwt_secret, iat=now - 7200, exp=now - 60)

    status, _, _ = request_url(
        "GET",
        f"{require_rale_router_url()}/registry/demo/package@abc123/some-file.txt",
        headers={"x-rale-taj": expired_taj},
    )
    assert status == 401


@pytest.mark.integration
def test_rale_router_rejects_tampered_taj() -> None:
    """RALE router returns 401 for a TAJ signed with the wrong secret."""
    now = int(time.time())
    tampered_taj = pyjwt.encode(
        {
            "sub": "test-user",
            "grants": ["s3:GetObject/registry/demo/package@abc123/"],
            "manifest_hash": "abc123",
            "package_name": "demo/package",
            "registry": "registry",
            "iat": now,
            "exp": now + 3600,
        },
        "wrong-secret",
        algorithm="HS256",
    )

    status, _, _ = request_url(
        "GET",
        f"{require_rale_router_url()}/registry/demo/package@abc123/some-file.txt",
        headers={"x-rale-taj": tampered_taj},
    )
    assert status == 401


@pytest.mark.integration
def test_rale_router_denies_mismatched_package() -> None:
    """RALE router returns 403 when the TAJ is for a different package than requested."""
    jwt_secret = fetch_jwks_secret()
    taj = _make_taj(
        jwt_secret,
        manifest_hash="hash-a",
        package_name="package-a",
        grants=["s3:GetObject/registry/package-a@hash-a/"],
    )

    status, _, _ = request_url(
        "GET",
        f"{require_rale_router_url()}/registry/package-b@hash-b/some-file.txt",
        headers={"x-rale-taj": taj},
    )
    assert status == 403


@pytest.mark.integration
def test_rale_router_denies_file_not_in_manifest() -> None:
    """RALE router returns 403 when the requested logical key is not in the manifest."""
    jwt_secret = fetch_jwks_secret()
    manifest_hash = uuid.uuid4().hex
    taj = _make_taj(
        jwt_secret,
        manifest_hash=manifest_hash,
        package_name="demo/package",
        grants=[f"s3:GetObject/registry/demo/package@{manifest_hash}/"],
    )

    boto3.resource("dynamodb").Table(require_manifest_cache_table()).put_item(
        Item={
            "manifest_hash": manifest_hash,
            "entries": {"other-file.txt": [{"bucket": "some-bucket", "key": "some-key"}]},
        }
    )

    status, _, _ = request_url(
        "GET",
        f"{require_rale_router_url()}/registry/demo/package@{manifest_hash}/nonexistent-file.txt",
        headers={"x-rale-taj": taj},
    )
    assert status == 403


# ---------------------------------------------------------------------------
# Control plane / audit
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_token_revocation_endpoint_available() -> None:
    status, _ = request_json("POST", "/token/revoke", {"token": "placeholder"})
    assert status == 200


@pytest.mark.integration
def test_admin_rotate_secret_invalidates_old_tokens() -> None:
    """Hard revocation rotates signing key epoch and invalidates existing tokens."""
    old_token, _ = issue_rajee_token()

    status, body = request_json("POST", "/admin/rotate-secret")
    assert status == 202, body
    operation_id = body.get("operation_id")
    assert isinstance(operation_id, str) and operation_id

    final_status: str | None = None
    final_body: dict[str, Any] = {}
    for _ in range(30):
        op_status, op_body = request_json("GET", f"/admin/rotate-secret/{operation_id}")
        assert op_status == 200, op_body
        state = op_body.get("status")
        if isinstance(state, str):
            final_status = state
            final_body = op_body
        if state in {"SUCCEEDED", "FAILED"}:
            break
        time.sleep(1)

    assert final_status == "SUCCEEDED", final_body

    # Old signatures must fail after cutover; newly issued tokens must pass.
    current_secret = fetch_jwks_secret()
    with pytest.raises(pyjwt.InvalidTokenError):
        pyjwt.decode(
            old_token,
            current_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )

    new_token, _ = issue_rajee_token()
    assert new_token != old_token
    pyjwt.decode(
        new_token,
        current_secret,
        algorithms=["HS256"],
        options={"verify_aud": False},
    )


@pytest.mark.integration
def test_policy_to_token_traceability() -> None:
    if not _cedar_tool_available():
        pytest.skip("cargo or CEDAR_PARSE_BIN is required for Cedar parsing")
    status, body = request_json("GET", "/policies", query={"include_statements": "true"})
    assert status == 200
    policies = body.get("policies", [])
    expected_scopes: dict[str, set[str]] = {}

    for policy in policies:
        statement = policy.get("definition", {}).get("static", {}).get("statement")
        if not statement:
            continue
        compiled = compile_policy(statement)
        for principal, scopes in compiled.items():
            expected_scopes.setdefault(principal, set()).update(scopes)

    token, scopes = issue_rajee_token()
    assert "test-user" in expected_scopes
    assert expected_scopes["test-user"].issubset(set(scopes))
    assert token


@pytest.mark.integration
def test_principal_scope_mapping_isolated() -> None:
    bucket = require_rajee_test_bucket()
    principal_a = f"mapping-a-{uuid.uuid4().hex}"
    principal_b = f"mapping-b-{uuid.uuid4().hex}"
    scopes_a = [f"S3Bucket:{bucket}:s3:ListBucket"]
    scopes_b = [f"S3Object:{bucket}/mapping/:s3:GetObject"]

    request_json("POST", "/principals", {"principal": principal_a, "scopes": scopes_a})
    request_json("POST", "/principals", {"principal": principal_b, "scopes": scopes_b})

    status, body = request_json("POST", "/token", {"principal": principal_a})
    assert status == 200
    assert set(body.get("scopes", [])) == set(scopes_a)


@pytest.mark.integration
def test_avp_policy_store_matches_local_files() -> None:
    from pathlib import Path

    status, body = request_json("GET", "/policies", query={"include_statements": "true"})
    assert status == 200
    remote_statements = {
        _normalize_statement(p.get("definition", {}).get("static", {}).get("statement", ""))
        for p in body.get("policies", [])
    }

    policy_root = Path(__file__).resolve().parents[2] / "policies"
    local_policy_files = [
        path for path in policy_root.glob("*.cedar") if path.name != "schema.cedar"
    ]
    local_statements = {
        _normalize_statement(stmt)
        for path in local_policy_files
        for stmt in _split_statements(path.read_text())
    }

    assert local_statements.issubset(remote_statements)


@pytest.mark.integration
def test_health_check_verifies_dependencies() -> None:
    status, body = request_json("GET", "/health")
    assert status == 200
    assert body.get("dependencies")


def _split_statements(policy_text: str) -> list[str]:
    return [f"{chunk.strip()};" for chunk in policy_text.split(";") if chunk.strip()]


def _normalize_statement(statement: str) -> str:
    normalized = "".join(statement.split()).rstrip(";")
    if "{{" in normalized:
        normalized = _expand_templates(normalized)
    return normalized
