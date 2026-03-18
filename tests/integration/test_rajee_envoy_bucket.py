"""Integration tests for RAJEE bucket access via the RALE routing stack."""

import json
import time
from urllib.parse import quote

import boto3
import jwt as pyjwt
import pytest
from botocore.exceptions import ClientError

from scripts.seed_packages import seed_files_for_package

from .helpers import (
    fetch_jwks_secret,
    parse_rale_test_quilt_uri,
    request_url,
    require_rajee_endpoint,
    require_rajee_test_bucket,
    require_rale_router_url,
    require_rale_test_quilt_uri,
    require_test_principal,
)


@pytest.mark.integration
def test_rajee_test_bucket_exists() -> None:
    bucket = require_rajee_test_bucket()
    s3 = boto3.client("s3")
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        pytest.fail(f"Expected RAJEE test bucket {bucket} to exist: {exc}")


@pytest.mark.integration
def test_rale_router_retrieves_object_from_test_bucket() -> None:
    """RALE router fetches the seeded object via a valid TAJ."""
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)
    logical_key = "data.csv"
    expected_body = seed_files_for_package(parts["package_name"])[logical_key]

    now = int(time.time())
    taj = pyjwt.encode(
        {
            "sub": require_test_principal(),
            "grants": [
                f"s3:GetObject/{parts['registry']}/{parts['package_name']}@{parts['hash']}/"
            ],
            "manifest_hash": parts["hash"],
            "package_name": parts["package_name"],
            "registry": parts["registry"],
            "iat": now,
            "exp": now + 3600,
        },
        fetch_jwks_secret(),
        algorithm="HS256",
    )

    usl_path = f"/{parts['registry']}/{parts['package_name']}@{parts['hash']}/{logical_key}"
    status, _, body = request_url(
        "GET",
        f"{require_rale_router_url()}{quote(usl_path, safe='/@')}",
        headers={"x-rale-taj": taj},
        sigv4=True,
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    assert body == expected_body


@pytest.mark.integration
def test_rale_authorizer_returns_taj_for_authorized_principal() -> None:
    """RALE authorizer returns a fresh TAJ for a DataZone-authorized principal."""
    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)
    principal = require_test_principal()

    usl_path = f"/{parts['registry']}/{parts['package_name']}/data.csv"
    status, _, body = request_url(
        "GET",
        f"{require_rajee_endpoint()}{quote(usl_path, safe='/@')}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    response = json.loads(body.decode("utf-8"))
    payload = pyjwt.decode(
        response["token"],
        fetch_jwks_secret(),
        algorithms=["HS256"],
        options={"verify_aud": False},
    )
    assert payload.get("sub") == principal
    assert response.get("cached") is False
