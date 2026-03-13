"""
Integration tests demonstrating package grant (RAJ-package) authorization.

This module demonstrates the full workflow for package-based authorization:
1. Create a package grant token anchored to an immutable Quilt package
2. Enforce authorization by checking S3 object membership in the package
3. Verify both allowed and denied access scenarios

Package grants solve the "policy explosion" problem by anchoring authority to
immutable package identifiers rather than enumerating thousands of file paths.
"""

import pytest

from raja.enforcer import enforce_package_grant
from raja.models import PackageAccessRequest
from raja.token import create_token_with_package_grant

from .helpers import fetch_jwks_secret


def mock_membership_checker_allow_all(quilt_uri: str, bucket: str, key: str) -> bool:
    """Mock membership checker that allows access to test objects."""
    # In production, this would resolve the quilt package manifest
    # and check if (bucket, key) is in the package
    if key.startswith("rajee-integration/package-demo/"):
        return True
    return False


def mock_membership_checker_specific_files(quilt_uri: str, bucket: str, key: str) -> bool:
    """Mock membership checker with explicit file list."""
    # Simulate a package containing only specific files
    package_files = {
        ("raja-poc-test-712023778557-us-east-1", "rajee-integration/package-demo/data.csv"),
        ("raja-poc-test-712023778557-us-east-1", "rajee-integration/package-demo/README.md"),
        ("raja-poc-test-712023778557-us-east-1", "rajee-integration/package-demo/results.json"),
    }
    return (bucket, key) in package_files


@pytest.mark.integration
def test_package_grant_allows_member_file():
    """
    Demonstrate successful package grant authorization.

    Workflow:
    1. Create RAJ-package token for immutable package
    2. Request access to S3 object that IS in the package
    3. Verify ALLOW decision with membership confirmation
    """
    secret = fetch_jwks_secret()

    # Create package grant token
    # In production, this quilt_uri would be resolved from a real Quilt package
    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123def456"
    token = create_token_with_package_grant(
        subject="User::demo-analyst",
        quilt_uri=quilt_uri,
        mode="read",
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("📦 PACKAGE GRANT AUTHORIZATION - ALLOW SCENARIO")
    print("=" * 80)
    print("\n[STEP 1] Package Grant Token Created")
    print("   Principal: User::demo-analyst")
    print(f"   Package URI: {quilt_uri}")
    print("   Mode: read")
    print(f"   Token length: {len(token)} chars")

    # Request access to a file that IS in the package
    request = PackageAccessRequest(
        bucket="raja-poc-test-712023778557-us-east-1",
        key="rajee-integration/package-demo/data.csv",
        action="s3:GetObject",
    )

    print("\n[STEP 2] Checking Package Membership")
    print(f"   S3 Object: s3://{request.bucket}/{request.key}")
    print(f"   Action: {request.action}")

    # Enforce authorization
    decision = enforce_package_grant(
        token_str=token,
        request=request,
        secret=secret,
        membership_checker=mock_membership_checker_allow_all,
    )

    print("\n[STEP 3] Authorization Decision")
    print(f"   Result: {'✅ ALLOWED' if decision.allowed else '🚫 DENIED'}")
    print(f"   Reason: {decision.reason}")
    if decision.matched_scope:
        print(f"   Matched Package: {decision.matched_scope}")

    print("\n" + "=" * 80)
    print("✅ PACKAGE GRANT CONFIRMED")
    print("   • Token anchored to immutable package identifier")
    print("   • S3 object is member of package")
    print("   • Authorization granted without enumerating files in policy")
    print("=" * 80)

    assert decision.allowed is True
    assert decision.reason == "object is member of package"
    assert decision.matched_scope == quilt_uri


@pytest.mark.integration
def test_package_grant_denies_non_member_file():
    """
    Demonstrate package grant denial for non-member files.

    Workflow:
    1. Create RAJ-package token for immutable package
    2. Request access to S3 object that is NOT in the package
    3. Verify DENY decision with clear reason
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123def456"
    token = create_token_with_package_grant(
        subject="User::demo-analyst",
        quilt_uri=quilt_uri,
        mode="read",
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("🚫 PACKAGE GRANT AUTHORIZATION - DENY SCENARIO")
    print("=" * 80)
    print("\n[STEP 1] Package Grant Token Created")
    print(f"   Package URI: {quilt_uri}")

    # Request access to a file that is NOT in the package
    request = PackageAccessRequest(
        bucket="raja-poc-test-712023778557-us-east-1",
        key="unauthorized-prefix/secret-data.csv",  # Not in package
        action="s3:GetObject",
    )

    print("\n[STEP 2] Checking Package Membership")
    print(f"   S3 Object: s3://{request.bucket}/{request.key}")
    print("   ⚠️  This object is NOT in the package")

    # Enforce authorization
    decision = enforce_package_grant(
        token_str=token,
        request=request,
        secret=secret,
        membership_checker=mock_membership_checker_allow_all,
    )

    print("\n[STEP 3] Authorization Decision")
    print(f"   Result: {'✅ ALLOWED' if decision.allowed else '🚫 DENIED'}")
    print(f"   Reason: {decision.reason}")

    print("\n" + "=" * 80)
    print("✅ PACKAGE GRANT DENIAL CONFIRMED")
    print("   • Token is valid and not expired")
    print("   • S3 object is NOT a member of the package")
    print("   • Authorization denied (fail-closed semantics)")
    print("=" * 80)

    assert decision.allowed is False
    assert decision.reason == "object not in package"


@pytest.mark.integration
def test_package_grant_with_specific_file_list():
    """
    Demonstrate package grant with explicit file membership.

    This test shows how package grants scale: one grant for N files,
    without enumerating files in the source grant.
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/experiment@def789ghi012"
    token = create_token_with_package_grant(
        subject="User::researcher",
        quilt_uri=quilt_uri,
        mode="read",
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("📋 PACKAGE GRANT WITH EXPLICIT FILE LIST")
    print("=" * 80)
    print("\n[STEP 1] Package Contains 3 Files")
    print("   • rajee-integration/package-demo/data.csv")
    print("   • rajee-integration/package-demo/README.md")
    print("   • rajee-integration/package-demo/results.json")

    test_cases = [
        ("rajee-integration/package-demo/data.csv", True, "File is in package"),
        ("rajee-integration/package-demo/README.md", True, "File is in package"),
        ("rajee-integration/package-demo/results.json", True, "File is in package"),
        ("rajee-integration/package-demo/secret.txt", False, "File NOT in package"),
        ("other-prefix/data.csv", False, "File NOT in package"),
    ]

    print("\n[STEP 2] Testing Access to Various Files")

    for key, expected_allow, description in test_cases:
        request = PackageAccessRequest(
            bucket="raja-poc-test-712023778557-us-east-1",
            key=key,
            action="s3:GetObject",
        )

        decision = enforce_package_grant(
            token_str=token,
            request=request,
            secret=secret,
            membership_checker=mock_membership_checker_specific_files,
        )

        status = "✅" if decision.allowed else "🚫"
        print(f"   {status} {key}: {description}")
        assert decision.allowed == expected_allow, f"Unexpected decision for {key}"

    print("\n" + "=" * 80)
    print("✅ PACKAGE GRANT SCALABILITY DEMONSTRATED")
    print("   • One policy grant covers multiple files")
    print("   • No file enumeration in the source grant")
    print("   • Package manifest defines exact membership")
    print("   • Scales to thousands of files without policy explosion")
    print("=" * 80)


@pytest.mark.integration
def test_package_grant_denies_write_operations():
    """
    Demonstrate that package grants with read mode deny write operations.

    Package grants are read-only by design (immutable packages).
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123"
    token = create_token_with_package_grant(
        subject="User::analyst",
        quilt_uri=quilt_uri,
        mode="read",
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("🚫 PACKAGE GRANT - WRITE OPERATIONS BLOCKED")
    print("=" * 80)

    # Try various write operations
    write_operations = [
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
    ]

    print("\n[TEST] Attempting Write Operations (mode=read)")

    for action in write_operations:
        request = PackageAccessRequest(
            bucket="raja-poc-test-712023778557-us-east-1",
            key="rajee-integration/package-demo/data.csv",
            action=action,
        )

        decision = enforce_package_grant(
            token_str=token,
            request=request,
            secret=secret,
            membership_checker=mock_membership_checker_allow_all,
        )

        print(f"   🚫 {action}: DENIED")
        assert decision.allowed is False
        assert decision.reason == "action not permitted by token mode"

    print("\n" + "=" * 80)
    print("✅ WRITE PROTECTION CONFIRMED")
    print("   • Package grants with mode=read block write operations")
    print("   • Immutable packages cannot be modified via authorization")
    print("=" * 80)
