"""
Integration tests demonstrating translation access grants (TAJ-package).

This module demonstrates the full workflow for logical-to-physical path translation:
1. Create a TAJ token anchored to an immutable Quilt package with logical paths
2. Enforce authorization by translating logical S3 paths to physical S3 locations
3. Verify translation works correctly and unauthorized paths are denied

Translation grants enable:
- Stable logical paths while physical storage changes
- Multi-region replication with consistent logical addressing
- Dataset versioning without breaking client code
"""

import pytest

from raja.enforcer import enforce_translation_grant
from raja.models import PackageAccessRequest, S3Location
from raja.package_map import PackageMap
from raja.token import create_token_with_package_map

from .helpers import fetch_jwks_secret


def mock_manifest_resolver_simple(quilt_uri: str) -> PackageMap:
    """
    Mock manifest resolver for demonstration.

    In production, this would:
    1. Parse the quilt_uri to extract package coordinates
    2. Fetch the package manifest from the registry
    3. Extract logical-to-physical mappings
    4. Return PackageMap with translation entries
    """
    # Simulate a package with logical → physical mappings
    return PackageMap(
        entries={
            "data/input.csv": [
                S3Location(
                    bucket="raja-poc-test-712023778557-us-east-1",
                    key="physical-storage/v1/dataset-abc123/input.csv",
                )
            ],
            "data/output.json": [
                S3Location(
                    bucket="raja-poc-test-712023778557-us-east-1",
                    key="physical-storage/v1/dataset-abc123/output.json",
                )
            ],
            "README.md": [
                S3Location(
                    bucket="raja-poc-test-712023778557-us-east-1",
                    key="physical-storage/v1/dataset-abc123/README.md",
                )
            ],
        }
    )


def mock_manifest_resolver_multi_region(quilt_uri: str) -> PackageMap:
    """
    Mock manifest resolver demonstrating multi-region replication.

    Same logical file can map to multiple physical locations
    (e.g., replicated across regions for performance/availability).
    """
    return PackageMap(
        entries={
            "data/large-file.csv": [
                # Primary location (us-east-1)
                S3Location(
                    bucket="raja-poc-test-712023778557-us-east-1",
                    key="replicated-data/us-east-1/large-file.csv",
                ),
                # Secondary location (us-west-2) - in a real scenario
                # This would be a different bucket in us-west-2
                S3Location(
                    bucket="raja-poc-test-712023778557-us-east-1",
                    key="replicated-data/us-west-2/large-file.csv",
                ),
            ],
        }
    )


@pytest.mark.integration
def test_translation_grant_allows_mapped_path():
    """
    Demonstrate successful translation grant authorization.

    Workflow:
    1. Create TAJ token for specific logical path
    2. Request access to logical S3 path
    3. Verify translation to physical S3 location(s)
    4. Confirm ALLOW decision with translated targets
    """
    secret = fetch_jwks_secret()

    # Create translation grant token
    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123def456"
    logical_bucket = "logical-dataset-namespace"
    logical_key = "data/input.csv"

    token = create_token_with_package_map(
        subject="User::data-engineer",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket=logical_bucket,
        logical_key=logical_key,
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("🔄 TRANSLATION ACCESS GRANT (TAJ) - SUCCESSFUL TRANSLATION")
    print("=" * 80)
    print("\n[STEP 1] Translation Grant Token Created")
    print("   Principal: User::data-engineer")
    print(f"   Package URI: {quilt_uri}")
    print(f"   Logical Path: s3://{logical_bucket}/{logical_key}")
    print("   Mode: read")

    # Request access to the logical path
    request = PackageAccessRequest(
        bucket=logical_bucket,
        key=logical_key,
        action="s3:GetObject",
    )

    print("\n[STEP 2] Resolving Package Manifest")
    print("   • Fetching manifest from registry (mocked)")
    print("   • Extracting logical-to-physical mappings")

    # Enforce authorization with translation
    decision = enforce_translation_grant(
        token_str=token,
        request=request,
        secret=secret,
        manifest_resolver=mock_manifest_resolver_simple,
    )

    print("\n[STEP 3] Translation Result")
    print(f"   Authorization: {'✅ ALLOWED' if decision.allowed else '🚫 DENIED'}")
    print(f"   Reason: {decision.reason}")

    if decision.translated_targets:
        print("\n   📍 Physical Target(s):")
        for target in decision.translated_targets:
            print(f"      • s3://{target.bucket}/{target.key}")

    print("\n" + "=" * 80)
    print("✅ TRANSLATION GRANT CONFIRMED")
    print("   • Logical path successfully translated to physical location")
    print("   • Client uses stable logical addressing")
    print("   • Physical storage can change without breaking clients")
    print("=" * 80)

    assert decision.allowed is True
    assert decision.reason == "logical object translated"
    assert decision.translated_targets is not None
    assert len(decision.translated_targets) == 1
    assert decision.translated_targets[0].bucket == "raja-poc-test-712023778557-us-east-1"
    assert decision.translated_targets[0].key == "physical-storage/v1/dataset-abc123/input.csv"


@pytest.mark.integration
def test_translation_grant_denies_unmapped_path():
    """
    Demonstrate translation grant denial for unmapped logical paths.

    Workflow:
    1. Create TAJ token for specific logical path
    2. Request access to DIFFERENT logical path (not in manifest)
    3. Verify DENY decision with clear reason
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123"
    logical_bucket = "logical-dataset-namespace"
    logical_key = "data/input.csv"

    token = create_token_with_package_map(
        subject="User::analyst",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket=logical_bucket,
        logical_key=logical_key,
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("🚫 TRANSLATION ACCESS GRANT - WRONG LOGICAL PATH")
    print("=" * 80)
    print("\n[STEP 1] TAJ Token Allows Only")
    print(f"   Logical Path: s3://{logical_bucket}/{logical_key}")

    # Request access to a DIFFERENT logical path
    request = PackageAccessRequest(
        bucket=logical_bucket,
        key="data/secret-file.csv",  # Not the authorized path
        action="s3:GetObject",
    )

    print("\n[STEP 2] Attempting Access to Different Path")
    print(f"   Requested: s3://{request.bucket}/{request.key}")
    print("   ⚠️  This path is NOT authorized by the token")

    decision = enforce_translation_grant(
        token_str=token,
        request=request,
        secret=secret,
        manifest_resolver=mock_manifest_resolver_simple,
    )

    print("\n[STEP 3] Authorization Decision")
    print("   Result: 🚫 DENIED")
    print(f"   Reason: {decision.reason}")

    print("\n" + "=" * 80)
    print("✅ PATH RESTRICTION CONFIRMED")
    print("   • TAJ token is scoped to specific logical path")
    print("   • Requests to other paths are denied")
    print("   • Fail-closed semantics enforced")
    print("=" * 80)

    assert decision.allowed is False
    assert decision.reason == "logical request not permitted by token"


@pytest.mark.integration
def test_translation_grant_denies_missing_manifest_entry():
    """
    Demonstrate translation grant denial when logical path is not in package manifest.

    This tests the case where:
    - Token authorizes the logical path
    - But the package manifest doesn't contain a mapping for it
    - Result: DENY (fail-closed)
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123"
    logical_bucket = "logical-dataset-namespace"
    logical_key = "data/missing-file.csv"  # Not in manifest

    token = create_token_with_package_map(
        subject="User::analyst",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket=logical_bucket,
        logical_key=logical_key,
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("🚫 TRANSLATION ACCESS GRANT - MISSING MANIFEST ENTRY")
    print("=" * 80)
    print("\n[STEP 1] TAJ Token Created")
    print(f"   Logical Path: s3://{logical_bucket}/{logical_key}")

    request = PackageAccessRequest(
        bucket=logical_bucket,
        key=logical_key,
        action="s3:GetObject",
    )

    print("\n[STEP 2] Checking Package Manifest")
    print("   ⚠️  Logical key NOT found in package manifest")

    decision = enforce_translation_grant(
        token_str=token,
        request=request,
        secret=secret,
        manifest_resolver=mock_manifest_resolver_simple,
    )

    print("\n[STEP 3] Authorization Decision")
    print("   Result: 🚫 DENIED")
    print(f"   Reason: {decision.reason}")

    print("\n" + "=" * 80)
    print("✅ MANIFEST VALIDATION CONFIRMED")
    print("   • Token is valid but logical key not in manifest")
    print("   • Translation failed (no physical target)")
    print("   • Authorization denied (fail-closed semantics)")
    print("=" * 80)

    assert decision.allowed is False
    assert decision.reason == "logical key not mapped in package"


@pytest.mark.integration
def test_translation_grant_multi_region_replication():
    """
    Demonstrate translation grant with multiple physical targets.

    This shows how TAJ can support:
    - Multi-region replication
    - Load balancing across storage locations
    - Disaster recovery failover
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/replicated-data@xyz789"
    logical_bucket = "logical-dataset-namespace"
    logical_key = "data/large-file.csv"

    token = create_token_with_package_map(
        subject="User::global-analyst",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket=logical_bucket,
        logical_key=logical_key,
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("🌍 TRANSLATION GRANT - MULTI-REGION REPLICATION")
    print("=" * 80)

    request = PackageAccessRequest(
        bucket=logical_bucket,
        key=logical_key,
        action="s3:GetObject",
    )

    print("\n[STEP 1] Logical Path Request")
    print(f"   s3://{logical_bucket}/{logical_key}")

    decision = enforce_translation_grant(
        token_str=token,
        request=request,
        secret=secret,
        manifest_resolver=mock_manifest_resolver_multi_region,
    )

    print("\n[STEP 2] Translation Result")
    print("   Authorization: ✅ ALLOWED")
    print(f"   Physical Targets: {len(decision.translated_targets or [])} location(s)")

    if decision.translated_targets:
        print("\n   📍 Replicated Locations:")
        for i, target in enumerate(decision.translated_targets, 1):
            print(f"      {i}. s3://{target.bucket}/{target.key}")

    print("\n" + "=" * 80)
    print("✅ MULTI-REGION TRANSLATION CONFIRMED")
    print("   • One logical path maps to multiple physical locations")
    print("   • Client unaware of replication topology")
    print("   • Downstream system can choose optimal location")
    print("   • Failover between regions transparent to client")
    print("=" * 80)

    assert decision.allowed is True
    assert decision.translated_targets is not None
    assert len(decision.translated_targets) == 2
    # Both targets should be present
    keys = [t.key for t in decision.translated_targets]
    assert "replicated-data/us-east-1/large-file.csv" in keys
    assert "replicated-data/us-west-2/large-file.csv" in keys


@pytest.mark.integration
def test_translation_grant_denies_write_operations():
    """
    Demonstrate that translation grants with read mode deny write operations.

    Translation grants are read-only by design (anchored to immutable packages).
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123"
    logical_bucket = "logical-dataset-namespace"
    logical_key = "data/input.csv"

    token = create_token_with_package_map(
        subject="User::analyst",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket=logical_bucket,
        logical_key=logical_key,
        ttl=300,
        secret=secret,
    )

    print("\n" + "=" * 80)
    print("🚫 TRANSLATION GRANT - WRITE OPERATIONS BLOCKED")
    print("=" * 80)

    write_operations = [
        "s3:PutObject",
        "s3:DeleteObject",
        "s3:DeleteObjectVersion",
    ]

    print("\n[TEST] Attempting Write Operations (mode=read)")

    for action in write_operations:
        request = PackageAccessRequest(
            bucket=logical_bucket,
            key=logical_key,
            action=action,
        )

        decision = enforce_translation_grant(
            token_str=token,
            request=request,
            secret=secret,
            manifest_resolver=mock_manifest_resolver_simple,
        )

        print(f"   🚫 {action}: DENIED")
        assert decision.allowed is False
        assert decision.reason == "action not permitted by token mode"

    print("\n" + "=" * 80)
    print("✅ WRITE PROTECTION CONFIRMED")
    print("   • TAJ tokens with mode=read block write operations")
    print("   • Immutable packages cannot be modified via translation")
    print("=" * 80)


@pytest.mark.integration
def test_translation_grant_multiple_files():
    """
    Demonstrate translation grant authorization for multiple logical files.

    Shows how TAJ scales to translate multiple logical paths in a package.
    """
    secret = fetch_jwks_secret()

    quilt_uri = "quilt+s3://registry#package=example/dataset@abc123"

    print("\n" + "=" * 80)
    print("📋 TRANSLATION GRANT - MULTIPLE FILE TRANSLATIONS")
    print("=" * 80)
    print("\n[STEP 1] Package Contains Multiple Logical Files")
    print("   • data/input.csv → physical-storage/v1/dataset-abc123/input.csv")
    print("   • data/output.json → physical-storage/v1/dataset-abc123/output.json")
    print("   • README.md → physical-storage/v1/dataset-abc123/README.md")

    test_cases = [
        ("data/input.csv", "physical-storage/v1/dataset-abc123/input.csv"),
        ("data/output.json", "physical-storage/v1/dataset-abc123/output.json"),
        ("README.md", "physical-storage/v1/dataset-abc123/README.md"),
    ]

    print("\n[STEP 2] Testing Translation for Each File")

    for logical_key, expected_physical_key in test_cases:
        # Create token for this logical path
        token = create_token_with_package_map(
            subject="User::engineer",
            quilt_uri=quilt_uri,
            mode="read",
            logical_bucket="logical-dataset-namespace",
            logical_key=logical_key,
            ttl=300,
            secret=secret,
        )

        request = PackageAccessRequest(
            bucket="logical-dataset-namespace",
            key=logical_key,
            action="s3:GetObject",
        )

        decision = enforce_translation_grant(
            token_str=token,
            request=request,
            secret=secret,
            manifest_resolver=mock_manifest_resolver_simple,
        )

        assert decision.allowed is True
        assert decision.translated_targets is not None
        assert len(decision.translated_targets) == 1
        assert decision.translated_targets[0].key == expected_physical_key

        print(f"   ✅ {logical_key}")
        print(f"      → {decision.translated_targets[0].key}")

    print("\n" + "=" * 80)
    print("✅ MULTI-FILE TRANSLATION CONFIRMED")
    print("   • Each logical file translates to correct physical location")
    print("   • Package manifest defines all mappings")
    print("   • Scales to thousands of files without policy explosion")
    print("=" * 80)
