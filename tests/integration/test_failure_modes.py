import time
from typing import Any

import jwt as pyjwt
import pytest
from botocore.exceptions import ClientError

from ..shared.s3_client import create_rajee_s3_client
from ..shared.token_builder import TokenBuilder
from .helpers import (
    fetch_jwks_secret,
    issue_rajee_token,
    parse_rale_test_quilt_uri,
    request_json,
    request_url,
    require_api_issuer,
    require_rajee_test_bucket,
    require_rale_router_url,
    require_rale_test_quilt_uri,
    require_test_principal,
)


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
        "sub": require_test_principal(),
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
        .with_subject(require_test_principal())
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
        .with_subject(require_test_principal())
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
        .with_subject(require_test_principal())
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
        .with_subject(require_test_principal())
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
        sigv4=True,
    )
    assert status == 401


@pytest.mark.integration
def test_rale_router_rejects_tampered_taj() -> None:
    """RALE router returns 401 for a TAJ signed with the wrong secret."""
    now = int(time.time())
    tampered_taj = pyjwt.encode(
        {
            "sub": require_test_principal(),
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
        sigv4=True,
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
        sigv4=True,
    )
    assert status == 403


@pytest.mark.integration
def test_rale_router_denies_file_not_in_manifest() -> None:
    """RALE router returns 403 when the logical key is absent from the seeded manifest."""
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)
    taj = _make_taj(
        fetch_jwks_secret(),
        manifest_hash=parts["hash"],
        package_name=parts["package_name"],
        registry=parts["registry"],
        grants=[f"s3:GetObject/{parts['registry']}/{parts['package_name']}@{parts['hash']}/"],
    )

    status, _, _ = request_url(
        "GET",
        (
            f"{require_rale_router_url()}/"
            f"{parts['registry']}/{parts['package_name']}@{parts['hash']}/nonexistent-file.txt"
        ),
        headers={"x-rale-taj": taj},
        sigv4=True,
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
    principal = require_test_principal()
    old_token = issue_rajee_token(principal)

    status, body = request_json("POST", "/admin/rotate-secret")
    assert status == 202, body
    assert body.get("status") == "SUCCEEDED", body

    # Old signatures must fail after cutover; newly issued tokens must pass.
    current_secret = fetch_jwks_secret()
    with pytest.raises(pyjwt.InvalidTokenError):
        pyjwt.decode(
            old_token,
            current_secret,
            algorithms=["HS256"],
            options={"verify_aud": False},
        )

    new_token = issue_rajee_token(principal)
    assert new_token != old_token
    pyjwt.decode(
        new_token,
        current_secret,
        algorithms=["HS256"],
        options={"verify_aud": False},
    )


@pytest.mark.integration
def test_package_listings_visible_via_control_plane() -> None:
    status, body = request_json("GET", "/policies", query={"include_statements": "true"})
    assert status == 200
    policies = body.get("policies", [])
    assert any(policy.get("type") == "datazone-listing" for policy in policies)


@pytest.mark.integration
def test_health_check_verifies_dependencies() -> None:
    status, body = request_json("GET", "/health")
    assert status == 200
    assert body.get("dependencies")
