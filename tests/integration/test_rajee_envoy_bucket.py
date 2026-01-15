import os
import uuid

import boto3
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from .helpers import require_rajee_endpoint, require_rajee_test_bucket

S3_UPSTREAM_HOST = "s3.us-east-1.amazonaws.com"


@pytest.mark.integration
def test_rajee_test_bucket_exists() -> None:
    bucket = require_rajee_test_bucket()
    s3 = boto3.client("s3")
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        pytest.fail(f"Expected RAJEE test bucket {bucket} to exist: {exc}")


@pytest.mark.integration
def test_rajee_envoy_s3_roundtrip_auth_disabled() -> None:
    bucket = require_rajee_test_bucket()
    endpoint = require_rajee_endpoint()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )
    s3.meta.events.register(
        "before-sign.s3",
        lambda request, **_: request.headers.__setitem__("Host", S3_UPSTREAM_HOST),
    )

    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    body = b"rajee-envoy-proxy-test"
    s3.put_object(Bucket=bucket, Key=key, Body=body)
    response = s3.get_object(Bucket=bucket, Key=key)
    assert response["Body"].read() == body
    s3.delete_object(Bucket=bucket, Key=key)
