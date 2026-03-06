"""
Integration tests for RAJEE bucket access via the RALE routing stack.
"""

import json
import time
import uuid
from urllib.parse import quote

import boto3
import jwt as pyjwt
import pytest
from botocore.exceptions import ClientError

from .helpers import (
    request_url,
    require_jwt_secret_arn,
    require_manifest_cache_table,
    require_rajee_endpoint,
    require_rajee_test_bucket,
    require_rale_router_url,
    require_taj_cache_table,
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
    """RALE router fetches an S3 object via manifest translation using a valid TAJ."""
    bucket = require_rajee_test_bucket()
    registry = "registry"
    package_name = "demo/bucket-test"
    manifest_hash = uuid.uuid4().hex
    logical_key = f"rale-bucket-test/{uuid.uuid4().hex}.txt"
    expected_body = b"rale-bucket-retrieval-test"

    source_key = f"rale-integration/{uuid.uuid4().hex}.txt"
    boto3.client("s3").put_object(Bucket=bucket, Key=source_key, Body=expected_body)

    boto3.resource("dynamodb").Table(require_manifest_cache_table()).put_item(
        Item={
            "manifest_hash": manifest_hash,
            "entries": {logical_key: [{"bucket": bucket, "key": source_key}]},
        }
    )

    jwt_secret = boto3.client("secretsmanager").get_secret_value(SecretId=require_jwt_secret_arn())[
        "SecretString"
    ]
    now = int(time.time())
    taj = pyjwt.encode(
        {
            "sub": "test-user",
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

    usl_path = f"/{registry}/{package_name}@{manifest_hash}/{logical_key}"
    status, _, body = request_url(
        "GET",
        f"{require_rale_router_url()}{quote(usl_path, safe='/@')}",
        headers={"x-rale-taj": taj},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    assert body == expected_body


@pytest.mark.integration
def test_rale_authorizer_returns_taj_for_authorized_principal() -> None:
    """RALE authorizer returns a cached TAJ when the principal has an entry in the cache."""
    registry = "registry"
    package_name = "demo/auth-test"
    manifest_hash = uuid.uuid4().hex
    logical_key = f"file-{uuid.uuid4().hex}.txt"
    principal = "test-user"

    jwt_secret = boto3.client("secretsmanager").get_secret_value(SecretId=require_jwt_secret_arn())[
        "SecretString"
    ]
    now = int(time.time())
    taj = pyjwt.encode(
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

    boto3.resource("dynamodb").Table(require_taj_cache_table()).put_item(
        Item={
            "cache_key": f"{principal}#{manifest_hash}",
            "taj": taj,
            "decision": "ALLOW",
            "ttl": now + 300,
        }
    )

    usl_path = f"/{registry}/{package_name}@{manifest_hash}/{logical_key}"
    status, _, body = request_url(
        "GET",
        f"{require_rajee_endpoint()}{quote(usl_path, safe='/@')}",
        headers={"x-raja-principal": principal},
    )
    assert status == 200, body.decode("utf-8", errors="replace")
    payload = json.loads(body.decode("utf-8"))
    assert payload.get("token") == taj
    assert payload.get("cached") is True
