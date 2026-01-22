"""Shared S3 client builder for RAJEE Envoy proxy testing.

This module provides a unified S3 client configuration for tests that
need to interact with the RAJEE Envoy S3 proxy.

Consolidates S3 client setup previously duplicated in:
- tests/integration/test_rajee_envoy_bucket.py
- tests/integration/test_failure_modes.py
"""

from __future__ import annotations

import os

# Add integration helpers to path
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.config import Config

integration_dir = Path(__file__).parent.parent / "integration"
if str(integration_dir) not in sys.path:
    sys.path.insert(0, str(integration_dir))

from helpers import require_rajee_endpoint, require_rajee_test_bucket  # noqa: E402

S3_UPSTREAM_HOST = "s3.us-east-1.amazonaws.com"


def create_rajee_s3_client(
    token: str | None = None,
    verbose: bool = False,
) -> tuple[Any, str]:
    """Create S3 client configured to use RAJEE Envoy proxy.

    This client is configured to:
    - Use the RAJEE Envoy proxy as the endpoint
    - Rewrite the Host header to point to the real S3 upstream
    - Inject the RAJA authorization token if provided
    - Use path-style addressing

    Args:
        token: Optional RAJA JWT token to include in requests
        verbose: If True, print configuration details

    Returns:
        Tuple of (boto3 S3 client, bucket name)

    Example:
        s3, bucket = create_rajee_s3_client(token="eyJ...")
        s3.put_object(Bucket=bucket, Key="test.txt", Body=b"hello")
    """
    bucket = require_rajee_test_bucket()
    endpoint = require_rajee_endpoint()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

    if verbose:
        print("\n" + "=" * 80)
        print("RAJEE ENVOY S3 PROXY CONFIGURATION")
        print("=" * 80)
        print(f"\n📡 Envoy Proxy Endpoint: {endpoint}")
        print(f"🪣 S3 Bucket: {bucket}")
        print(f"🌎 Region: {region}")
        print(f"🔄 Host Header Rewrite: Envoy → {S3_UPSTREAM_HOST}")
        if token:
            print(f"🔐 Authorization: Bearer {token[:20]}...")
        print("\n" + "-" * 80)

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )

    def _apply_headers(request: Any, **_: Any) -> None:
        """Apply custom headers before signing request."""
        request.headers.__setitem__("Host", S3_UPSTREAM_HOST)
        if token is not None:
            request.headers.__setitem__("x-raja-authorization", f"Bearer {token}")

    s3.meta.events.register("before-sign.s3", _apply_headers)
    return s3, bucket


def create_rajee_s3_client_with_region(
    token: str | None = None,
    verbose: bool = False,
) -> tuple[Any, str, str]:
    """Create S3 client with region info included.

    Same as create_rajee_s3_client but also returns the region.

    Args:
        token: Optional RAJA JWT token
        verbose: If True, print configuration details

    Returns:
        Tuple of (boto3 S3 client, bucket name, region)
    """
    s3, bucket = create_rajee_s3_client(token=token, verbose=verbose)
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    return s3, bucket, region
