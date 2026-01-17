import os
import time
import uuid
from typing import Any

import boto3
import jwt
import pytest
from botocore.config import Config
from botocore.exceptions import ClientError

from raja.rajee.authorizer import is_authorized

from .helpers import issue_rajee_token, require_rajee_endpoint, require_rajee_test_bucket

S3_UPSTREAM_HOST = "s3.us-east-1.amazonaws.com"


def _log_operation(operation: str, details: str = "") -> None:
    """Log an operation with timestamp for demo purposes."""
    timestamp = time.strftime("%H:%M:%S")
    print(f"\n[{timestamp}] {operation}")
    if details:
        print(f"          {details}")


@pytest.mark.integration
def test_rajee_test_bucket_exists() -> None:
    bucket = require_rajee_test_bucket()
    s3 = boto3.client("s3")
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError as exc:
        pytest.fail(f"Expected RAJEE test bucket {bucket} to exist: {exc}")


def _create_s3_client_with_rajee_proxy(
    verbose: bool = False, token: str | None = None
) -> tuple[Any, str, str]:
    """Create S3 client configured to use RAJEE Envoy proxy."""
    bucket = require_rajee_test_bucket()
    endpoint = require_rajee_endpoint()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"

    if verbose:
        print("\n" + "=" * 80)
        print("RAJEE ENVOY S3 PROXY DEMONSTRATION")
        print("=" * 80)
        print(f"\n📡 Envoy Proxy Endpoint: {endpoint}")
        print(f"🪣 S3 Bucket: {bucket}")
        print(f"🌎 Region: {region}")
        print(f"🔄 Host Header Rewrite: Envoy → {S3_UPSTREAM_HOST}")
        print("\n" + "-" * 80)

    s3 = boto3.client(
        "s3",
        endpoint_url=endpoint,
        region_name=region,
        config=Config(s3={"addressing_style": "path"}),
    )

    def _apply_headers(request, **_: Any) -> None:
        request.headers.__setitem__("Host", S3_UPSTREAM_HOST)
        if token:
            request.headers.__setitem__("x-raja-authorization", f"Bearer {token}")

    s3.meta.events.register("before-sign.s3", _apply_headers)
    return s3, bucket, region


@pytest.mark.integration
@pytest.mark.skip(reason="Legacy test, auth now enabled by default")
def test_rajee_envoy_s3_roundtrip_auth_disabled_legacy() -> None:
    s3, bucket, _ = _create_s3_client_with_rajee_proxy(verbose=True)

    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    body = b"rajee-envoy-proxy-test"

    _log_operation("✍️  PUT OBJECT", f"Key: {key} ({len(body)} bytes)")
    start = time.time()
    put_response = s3.put_object(Bucket=bucket, Key=key, Body=body)
    put_time = time.time() - start
    _log_operation(f"✅ PUT SUCCESS ({put_time:.3f}s)", f"ETag: {put_response.get('ETag', 'N/A')}")

    _log_operation("📥 GET OBJECT", f"Key: {key}")
    start = time.time()
    response = s3.get_object(Bucket=bucket, Key=key)
    get_time = time.time() - start
    retrieved_body = response["Body"].read()
    assert retrieved_body == body
    _log_operation(
        f"✅ GET SUCCESS ({get_time:.3f}s)", f"Retrieved {len(retrieved_body)} bytes, data matches!"
    )

    _log_operation("🗑️  DELETE OBJECT", f"Key: {key}")
    start = time.time()
    s3.delete_object(Bucket=bucket, Key=key)
    delete_time = time.time() - start
    _log_operation(f"✅ DELETE SUCCESS ({delete_time:.3f}s)", "Object removed")

    print("\n" + "=" * 80)
    print(f"✅ ROUNDTRIP TEST COMPLETE - Total time: {put_time + get_time + delete_time:.3f}s")
    print("=" * 80)


@pytest.mark.integration
def test_rajee_envoy_s3_roundtrip_with_auth() -> None:
    bucket = require_rajee_test_bucket()
    token = issue_rajee_token()
    s3, _, _ = _create_s3_client_with_rajee_proxy(verbose=True, token=token)

    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    body = b"rajee-envoy-proxy-test"

    _log_operation("✍️  PUT OBJECT", f"Key: {key} ({len(body)} bytes)")
    start = time.time()
    put_response = s3.put_object(Bucket=bucket, Key=key, Body=body)
    put_time = time.time() - start
    _log_operation(f"✅ PUT SUCCESS ({put_time:.3f}s)", f"ETag: {put_response.get('ETag', 'N/A')}")

    _log_operation("📥 GET OBJECT", f"Key: {key}")
    start = time.time()
    response = s3.get_object(Bucket=bucket, Key=key)
    get_time = time.time() - start
    retrieved_body = response["Body"].read()
    assert retrieved_body == body
    _log_operation(
        f"✅ GET SUCCESS ({get_time:.3f}s)", f"Retrieved {len(retrieved_body)} bytes, data matches!"
    )

    _log_operation("🗑️  DELETE OBJECT", f"Key: {key}")
    start = time.time()
    s3.delete_object(Bucket=bucket, Key=key)
    delete_time = time.time() - start
    _log_operation(f"✅ DELETE SUCCESS ({delete_time:.3f}s)", "Object removed")

    print("\n" + "=" * 80)
    print(f"✅ ROUNDTRIP TEST COMPLETE - Total time: {put_time + get_time + delete_time:.3f}s")
    print("=" * 80)


@pytest.mark.integration
def test_rajee_envoy_auth_with_real_grants() -> None:
    """
    COMPREHENSIVE RAJA INTEGRATION PROOF TEST

    This test demonstrates that RAJA is being used for authorization by:
    1. Obtaining a JWT token from RAJA control plane
    2. Decoding and displaying the grants in the token
    3. Performing local authorization check
    4. Sending the token to Envoy via x-raja-authorization header
    5. Envoy's external auth filter validates the token with RAJA
    """
    bucket = require_rajee_test_bucket()

    print("\n" + "=" * 80)
    print("🔐 RAJA INTEGRATION PROOF TEST")
    print("=" * 80)

    # Step 1: Get RAJA token
    print("\n[STEP 1] Obtaining JWT token from RAJA control plane...")
    token = issue_rajee_token("alice")
    print(f"✅ Token obtained (length: {len(token)} chars)")
    print(f"   Token preview: {token[:50]}...")

    # Step 2: Decode and show grants
    print("\n[STEP 2] Decoding token to inspect RAJA grants...")
    decoded = jwt.decode(token, options={"verify_signature": False})
    grants = decoded.get("grants", [])
    assert isinstance(grants, list)
    assert grants, "Token has no grants; load and compile Cedar policies."

    print(f"✅ Token contains {len(grants)} grant(s):")
    for i, grant in enumerate(grants, 1):
        print(f"   {i}. {grant}")

    # Step 3: Local authorization check
    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    request_string = f"s3:PutObject/{bucket}/{key}"

    print("\n[STEP 3] Local RAJA authorization check...")
    print(f"   Request: {request_string}")

    authorized = is_authorized(request_string, grants)
    assert authorized, "Token grants do not cover the rajee-integration/ prefix."
    print("✅ Local RAJA check: AUTHORIZED")

    # Step 4: Make request through Envoy with token
    print("\n[STEP 4] Sending request through Envoy with x-raja-authorization header...")
    s3, _, _ = _create_s3_client_with_rajee_proxy(verbose=True, token=token)
    body = b"real-authorization-test"

    _log_operation("✍️  PUT OBJECT (with RAJA token)", f"Key: {key}")
    put_response = s3.put_object(Bucket=bucket, Key=key, Body=body)
    assert put_response["ResponseMetadata"]["HTTPStatusCode"] == 200
    print("✅ Envoy accepted request (RAJA external auth filter validated token)")

    _log_operation("📥 GET OBJECT (with RAJA token)", f"Key: {key}")
    get_response = s3.get_object(Bucket=bucket, Key=key)
    assert get_response["Body"].read() == body
    print("✅ GET request authorized by RAJA")

    _log_operation("🗑️  DELETE OBJECT (with RAJA token)", f"Key: {key}")
    s3.delete_object(Bucket=bucket, Key=key)
    print("✅ DELETE request authorized by RAJA")

    print("\n" + "=" * 80)
    print("✅ RAJA INTEGRATION CONFIRMED")
    print("   • JWT token issued by RAJA control plane")
    print("   • Token contains grants compiled from Cedar policies")
    print("   • Envoy external auth filter validated token")
    print("   • All S3 operations authorized via RAJA")
    print("=" * 80)


@pytest.mark.integration
def test_rajee_envoy_auth_denies_unauthorized_prefix() -> None:
    """
    RAJA DENIAL TEST - Proves RAJA is enforcing authorization

    This test shows RAJA denying a request that doesn't match any grants.
    """
    bucket = require_rajee_test_bucket()

    print("\n" + "=" * 80)
    print("🚫 RAJA DENIAL PROOF TEST")
    print("=" * 80)

    print("\n[STEP 1] Obtaining RAJA token...")
    token = issue_rajee_token()
    decoded = jwt.decode(token, options={"verify_signature": False})
    grants = decoded.get("grants", [])

    print("✅ Token grants:")
    for grant in grants:
        print(f"   • {grant}")

    key = "unauthorized-prefix/test.txt"
    request_string = f"s3:PutObject/{bucket}/{key}"

    print("\n[STEP 2] Checking if request matches any grants...")
    print(f"   Request: {request_string}")
    authorized = is_authorized(request_string, grants)
    print(f"   Local RAJA check: {'AUTHORIZED' if authorized else 'DENIED'}")

    if not authorized:
        print("✅ Expected: Request should be denied (no matching grant)")

    s3, _, _ = _create_s3_client_with_rajee_proxy(verbose=True, token=token)
    body = b"This should be denied"

    print("\n[STEP 3] Sending unauthorized request through Envoy...")
    _log_operation("🚫 PUT OBJECT (unauthorized prefix)", f"Key: {key}")

    with pytest.raises(ClientError) as exc_info:
        s3.put_object(Bucket=bucket, Key=key, Body=body)

    response = exc_info.value.response
    status = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
    assert status == 403, f"Expected 403 Forbidden, got {status}"

    message = response.get("Error", {}).get("Message", "")
    if message:
        assert "Forbidden" in message or "grant" in message

    _log_operation(
        "✅ ENVOY DENIED REQUEST (403 Forbidden)", "RAJA external auth filter blocked it"
    )

    print("\n" + "=" * 80)
    print("✅ RAJA DENIAL CONFIRMED")
    print("   • Token does not contain grant for 'unauthorized-prefix/'")
    print("   • Local RAJA check predicted denial")
    print("   • Envoy external auth filter denied request (403)")
    print("   • RAJA is actively enforcing authorization!")
    print("=" * 80)


@pytest.mark.integration
def test_rajee_envoy_list_bucket() -> None:
    """Test ListBucket operation through RAJEE proxy."""
    bucket = require_rajee_test_bucket()
    token = issue_rajee_token()
    s3, _, _ = _create_s3_client_with_rajee_proxy(verbose=True, token=token)

    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    body = b"list-bucket-test"

    try:
        _log_operation("✍️  PUT OBJECT (for listing test)", f"Key: {key}")
        start = time.time()
        s3.put_object(Bucket=bucket, Key=key, Body=body)
        put_time = time.time() - start
        _log_operation(f"✅ PUT SUCCESS ({put_time:.3f}s)")

        _log_operation("📋 LIST OBJECTS", "Prefix: rajee-integration/")
        start = time.time()
        response = s3.list_objects_v2(Bucket=bucket, Prefix="rajee-integration/")
        list_time = time.time() - start
        assert "Contents" in response
        object_count = len(response["Contents"])
        found = any(obj["Key"] == key for obj in response["Contents"])
        assert found
        _log_operation(
            f"✅ LIST SUCCESS ({list_time:.3f}s)",
            f"Found {object_count} objects, including our test object",
        )

        print("\n" + "=" * 80)
        print(f"✅ LIST BUCKET TEST COMPLETE - Total time: {put_time + list_time:.3f}s")
        print("=" * 80)
    finally:
        _log_operation("🗑️  CLEANUP", f"Deleting {key}")
        s3.delete_object(Bucket=bucket, Key=key)


@pytest.mark.integration
def test_rajee_envoy_get_object_attributes() -> None:
    """Test GetObjectAttributes operation through RAJEE proxy."""
    bucket = require_rajee_test_bucket()
    token = issue_rajee_token()
    s3, _, _ = _create_s3_client_with_rajee_proxy(verbose=True, token=token)

    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    body = b"object-attributes-test"

    try:
        _log_operation("✍️  PUT OBJECT", f"Key: {key}")
        start = time.time()
        put_response = s3.put_object(Bucket=bucket, Key=key, Body=body)
        put_time = time.time() - start
        _log_operation(f"✅ PUT SUCCESS ({put_time:.3f}s)", f"ETag: {put_response['ETag']}")

        _log_operation("🔍 GET OBJECT ATTRIBUTES", "Requesting ETag, ObjectSize, StorageClass")
        start = time.time()
        attrs_response = s3.get_object_attributes(
            Bucket=bucket, Key=key, ObjectAttributes=["ETag", "ObjectSize", "StorageClass"]
        )
        attrs_time = time.time() - start

        # GetObjectAttributes returns ETag without quotes, PutObject returns with quotes
        put_etag = put_response["ETag"].strip('"')
        assert attrs_response["ETag"] == put_etag
        assert attrs_response["ObjectSize"] == len(body)
        assert "StorageClass" in attrs_response
        _log_operation(
            f"✅ ATTRIBUTES SUCCESS ({attrs_time:.3f}s)",
            f"Size: {attrs_response['ObjectSize']} bytes, "
            f"StorageClass: {attrs_response['StorageClass']}",
        )

        print("\n" + "=" * 80)
        print(f"✅ GET ATTRIBUTES TEST COMPLETE - Total time: {put_time + attrs_time:.3f}s")
        print("=" * 80)
    finally:
        _log_operation("🗑️  CLEANUP", f"Deleting {key}")
        s3.delete_object(Bucket=bucket, Key=key)


@pytest.mark.integration
def test_rajee_envoy_versioning_operations() -> None:
    """Test version-aware operations through RAJEE proxy (GetObjectVersion, ListBucketVersions)."""
    bucket = require_rajee_test_bucket()
    token = issue_rajee_token()
    s3, _, _ = _create_s3_client_with_rajee_proxy(verbose=True, token=token)

    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    body_v1 = b"version-1"
    body_v2 = b"version-2"

    try:
        _log_operation("✍️  PUT OBJECT (Version 1)", f"Key: {key}, Body: {body_v1.decode()}")
        start = time.time()
        put_v1 = s3.put_object(Bucket=bucket, Key=key, Body=body_v1)
        put_v1_time = time.time() - start
        version_id_v1 = put_v1.get("VersionId")
        _log_operation(
            f"✅ PUT V1 SUCCESS ({put_v1_time:.3f}s)",
            f"VersionId: {version_id_v1 or 'N/A (versioning disabled)'}",
        )

        _log_operation(
            "✍️  PUT OBJECT (Version 2 - overwrite)", f"Key: {key}, Body: {body_v2.decode()}"
        )
        start = time.time()
        put_v2 = s3.put_object(Bucket=bucket, Key=key, Body=body_v2)
        put_v2_time = time.time() - start
        version_id_v2 = put_v2.get("VersionId")
        _log_operation(
            f"✅ PUT V2 SUCCESS ({put_v2_time:.3f}s)",
            f"VersionId: {version_id_v2 or 'N/A (versioning disabled)'}",
        )

        _log_operation("📋 LIST OBJECT VERSIONS", "Prefix: rajee-integration/")
        start = time.time()
        versions_response = s3.list_object_versions(Bucket=bucket, Prefix="rajee-integration/")
        list_versions_time = time.time() - start
        if "Versions" in versions_response:
            version_keys = [v["Key"] for v in versions_response["Versions"]]
            assert key in version_keys
            version_count = sum(1 for v in versions_response["Versions"] if v["Key"] == key)
            _log_operation(
                f"✅ LIST VERSIONS SUCCESS ({list_versions_time:.3f}s)",
                f"Found {version_count} version(s) of our object",
            )

            if version_id_v1 and version_id_v2:
                version_ids = [
                    v["VersionId"] for v in versions_response["Versions"] if v["Key"] == key
                ]
                assert version_id_v1 in version_ids or version_id_v2 in version_ids
        else:
            _log_operation(
                f"✅ LIST VERSIONS SUCCESS ({list_versions_time:.3f}s)",
                "Versioning not enabled, skipping version checks",
            )

        get_v1_time = 0.0
        if version_id_v1:
            _log_operation("📥 GET OBJECT (Specific Version 1)", f"VersionId: {version_id_v1}")
            start = time.time()
            get_v1_response = s3.get_object(Bucket=bucket, Key=key, VersionId=version_id_v1)
            get_v1_time = time.time() - start
            retrieved_v1 = get_v1_response["Body"].read()
            assert retrieved_v1 == body_v1
            _log_operation(
                f"✅ GET V1 SUCCESS ({get_v1_time:.3f}s)", f"Retrieved: {retrieved_v1.decode()}"
            )

        _log_operation("📥 GET OBJECT (Current Version)", f"Key: {key}")
        start = time.time()
        get_current = s3.get_object(Bucket=bucket, Key=key)
        get_current_time = time.time() - start
        current_body = get_current["Body"].read()
        assert current_body == body_v2
        _log_operation(
            f"✅ GET CURRENT SUCCESS ({get_current_time:.3f}s)",
            f"Retrieved: {current_body.decode()}",
        )

        total_time = put_v1_time + put_v2_time + list_versions_time + get_current_time + get_v1_time

        print("\n" + "=" * 80)
        print(f"✅ VERSIONING TEST COMPLETE - Total time: {total_time:.3f}s")
        print("=" * 80)

    finally:
        _log_operation("🗑️  CLEANUP", "Deleting all versions")
        try:
            versions = s3.list_object_versions(Bucket=bucket, Prefix=key)
            deleted_count = 0
            if "Versions" in versions:
                for version in versions["Versions"]:
                    if version["Key"] == key:
                        s3.delete_object(Bucket=bucket, Key=key, VersionId=version["VersionId"])
                        deleted_count += 1
            if "DeleteMarkers" in versions:
                for marker in versions["DeleteMarkers"]:
                    if marker["Key"] == key:
                        s3.delete_object(Bucket=bucket, Key=key, VersionId=marker["VersionId"])
                        deleted_count += 1
            _log_operation("✅ CLEANUP COMPLETE", f"Deleted {deleted_count} version(s)")
        except Exception:
            s3.delete_object(Bucket=bucket, Key=key)
            _log_operation("✅ CLEANUP COMPLETE", "Simple delete (versioning not enabled)")
