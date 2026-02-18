from __future__ import annotations

import json
import time
import uuid
from urllib.parse import quote

import boto3
import jwt
import pytest

from .helpers import (
    request_url,
    require_jwt_secret_arn,
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

    secret_arn = require_jwt_secret_arn()
    jwt_secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)[
        "SecretString"
    ]

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

    secret_arn = require_jwt_secret_arn()
    jwt_secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)[
        "SecretString"
    ]

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

    secret_arn = require_jwt_secret_arn()
    jwt_secret = boto3.client("secretsmanager").get_secret_value(SecretId=secret_arn)[
        "SecretString"
    ]

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
