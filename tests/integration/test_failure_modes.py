import os
import shutil
import time
import uuid
from pathlib import Path
from urllib import error, request

import boto3
import jwt
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from raja.compiler import _expand_templates, compile_policy

from .helpers import (
    fetch_jwks_secret,
    issue_rajee_token,
    request_json,
    require_api_issuer,
    require_rajee_endpoint,
    require_rajee_test_bucket,
)


def _cedar_tool_available() -> bool:
    return bool(shutil.which("cargo")) or bool(os.environ.get("CEDAR_PARSE_BIN"))


S3_UPSTREAM_HOST = "s3.us-east-1.amazonaws.com"


def _create_s3_client_with_rajee_proxy(token: str | None) -> tuple[boto3.client, str]:
    bucket = require_rajee_test_bucket()
    endpoint = require_rajee_endpoint()
    region = "us-east-1"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )

    def _apply_headers(request, **_: object) -> None:
        request.headers.__setitem__("Host", S3_UPSTREAM_HOST)
        if token is not None:
            request.headers.__setitem__("x-raja-authorization", f"Bearer {token}")

    s3.meta.events.register("before-sign.s3", _apply_headers)
    return s3, bucket


def _list_bucket_status(token: str | None) -> int:
    s3, bucket = _create_s3_client_with_rajee_proxy(token)
    try:
        s3.list_objects_v2(Bucket=bucket, Prefix="rajee-integration/", MaxKeys=1)
        return 200
    except ClientError as exc:
        return exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0)


def _build_token(
    *,
    secret: str,
    issuer: str,
    audience: str,
    subject: str | None,
    scopes: object | None,
    include_scopes: bool = True,
    issued_at: int | None = None,
    expires_at: int | None = None,
) -> str:
    now = issued_at or int(time.time())
    exp = expires_at or now + 3600
    payload = {"iss": issuer, "aud": audience, "iat": now, "exp": exp}
    if subject is not None:
        payload["sub"] = subject
    if include_scopes:
        payload["scopes"] = scopes
    return jwt.encode(payload, secret, algorithm="HS256")


@pytest.mark.integration
def test_envoy_rejects_expired_token() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    bucket = require_rajee_test_bucket()
    token = _build_token(
        secret=secret,
        issuer=issuer,
        audience="raja-s3-proxy",
        subject="test-user",
        scopes=[f"S3Bucket:{bucket}:s3:ListBucket"],
        issued_at=int(time.time()) - 3600,
        expires_at=int(time.time()) - 60,
    )
    assert _list_bucket_status(token) == 401


@pytest.mark.integration
def test_envoy_rejects_invalid_signature() -> None:
    issuer = require_api_issuer()
    bucket = require_rajee_test_bucket()
    token = _build_token(
        secret="wrong-secret",
        issuer=issuer,
        audience="raja-s3-proxy",
        subject="test-user",
        scopes=[f"S3Bucket:{bucket}:s3:ListBucket"],
    )
    assert _list_bucket_status(token) == 401


@pytest.mark.integration
@pytest.mark.parametrize(
    "token",
    ["not.a.jwt", "header.payload", "!!!.***.$$$", ""],
)
def test_envoy_rejects_malformed_tokens(token: str) -> None:
    assert _list_bucket_status(token) == 401


@pytest.mark.integration
def test_envoy_denies_missing_scopes_claim() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    token = _build_token(
        secret=secret,
        issuer=issuer,
        audience="raja-s3-proxy",
        subject="test-user",
        scopes=None,
        include_scopes=False,
    )
    assert _list_bucket_status(token) == 403


@pytest.mark.integration
def test_envoy_denies_empty_scopes() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    token = _build_token(
        secret=secret,
        issuer=issuer,
        audience="raja-s3-proxy",
        subject="test-user",
        scopes=[],
    )
    assert _list_bucket_status(token) == 403


@pytest.mark.integration
def test_envoy_denies_null_scopes() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    token = _build_token(
        secret=secret,
        issuer=issuer,
        audience="raja-s3-proxy",
        subject="test-user",
        scopes=None,
    )
    assert _list_bucket_status(token) == 403


@pytest.mark.integration
def test_envoy_rejects_wrong_issuer() -> None:
    secret = fetch_jwks_secret()
    bucket = require_rajee_test_bucket()
    token = _build_token(
        secret=secret,
        issuer="https://wrong-issuer.example.com",
        audience="raja-s3-proxy",
        subject="test-user",
        scopes=[f"S3Bucket:{bucket}:s3:ListBucket"],
    )
    assert _list_bucket_status(token) == 401


@pytest.mark.integration
def test_envoy_rejects_wrong_audience() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    bucket = require_rajee_test_bucket()
    token = _build_token(
        secret=secret,
        issuer=issuer,
        audience="wrong-audience",
        subject="test-user",
        scopes=[f"S3Bucket:{bucket}:s3:ListBucket"],
    )
    assert _list_bucket_status(token) == 403


@pytest.mark.integration
def test_envoy_rejects_missing_subject() -> None:
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()
    bucket = require_rajee_test_bucket()
    token = _build_token(
        secret=secret,
        issuer=issuer,
        audience="raja-s3-proxy",
        subject=None,
        scopes=[f"S3Bucket:{bucket}:s3:ListBucket"],
    )
    assert _list_bucket_status(token) == 401


@pytest.mark.integration
def test_token_revocation_endpoint_available() -> None:
    status, _ = request_json("POST", "/token/revoke", {"token": "placeholder"})
    assert status == 200


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
def test_policy_update_invalidates_existing_token() -> None:
    bucket = require_rajee_test_bucket()
    principal = f"update-test-{uuid.uuid4().hex}"
    prefix = f"rajee-integration/{uuid.uuid4().hex}/"
    scopes = [
        f"S3Object:{bucket}/{prefix}:s3:PutObject",
        f"S3Object:{bucket}/{prefix}:s3:DeleteObject",
    ]

    request_json("POST", "/principals", {"principal": principal, "scopes": scopes})
    status, body = request_json("POST", "/token", {"principal": principal, "token_type": "rajee"})
    assert status == 200
    token = body.get("token")
    assert token

    s3, _ = _create_s3_client_with_rajee_proxy(token)
    key = f"{prefix}{uuid.uuid4().hex}.txt"
    s3.put_object(Bucket=bucket, Key=key, Body=b"policy-update-test")

    request_json("POST", "/principals", {"principal": principal, "scopes": []})
    s3.put_object(Bucket=bucket, Key=f"{prefix}{uuid.uuid4().hex}.txt", Body=b"still-allowed")

    s3.delete_object(Bucket=bucket, Key=key)


@pytest.mark.integration
def test_avp_policy_store_matches_local_files() -> None:
    status, body = request_json("GET", "/policies", query={"include_statements": "true"})
    assert status == 200
    remote_policies = body.get("policies", [])
    remote_statements = {
        _normalize_statement(policy.get("definition", {}).get("static", {}).get("statement", ""))
        for policy in remote_policies
    }

    local_statements = {
        _normalize_statement(statement)
        for path in _policy_files()
        for statement in _split_statements(path.read_text())
    }

    assert local_statements.issubset(remote_statements)


def _policy_files() -> list[Path]:
    policy_root = Path(__file__).resolve().parents[2] / "policies"
    return [path for path in policy_root.glob("*.cedar") if path.name != "schema.cedar"]


def _split_statements(policy_text: str) -> list[str]:
    statements: list[str] = []
    for chunk in policy_text.split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(f"{statement};")
    return statements


def _normalize_statement(statement: str) -> str:
    normalized = "".join(statement.split()).rstrip(";")
    if "{{" in normalized:
        normalized = _expand_templates(normalized)
    return normalized


@pytest.mark.integration
def test_error_response_format_is_s3_compatible() -> None:
    endpoint = require_rajee_endpoint()
    token, _ = issue_rajee_token()
    url = f"{endpoint}/invalid-bucket"
    req = request.Request(url, method="GET")
    req.add_header("Host", S3_UPSTREAM_HOST)
    req.add_header("x-raja-authorization", f"Bearer {token}")
    try:
        with request.urlopen(req) as response:
            _ = response.read()
            status = response.status
            content_type = response.headers.get("Content-Type")
    except error.HTTPError as exc:
        status = exc.code
        content_type = exc.headers.get("Content-Type")

    assert status == 403
    assert content_type == "application/xml"


@pytest.mark.integration
def test_health_check_verifies_dependencies() -> None:
    status, body = request_json("GET", "/health")
    assert status == 200
    assert body.get("dependencies")
