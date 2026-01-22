"""Tests for forbid policy support (Phase 3)."""

import os
import shutil

import pytest

from raja.compiler import compile_policies


def _cedar_tool_available() -> bool:
    return bool(shutil.which("cargo")) or bool(os.environ.get("CEDAR_PARSE_BIN"))


pytestmark = pytest.mark.skipif(
    not _cedar_tool_available(), reason="cargo or CEDAR_PARSE_BIN is required for Cedar parsing"
)


def test_compile_forbid_policy_with_flag():
    """Test that forbid policies compile when handle_forbids=True."""
    policy = (
        'forbid(principal == User::"alice", action == Action::"s3:DeleteObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    result = compile_policies([policy], handle_forbids=True)
    # Forbid-only policies result in empty grants
    assert result == {}


def test_compile_forbid_policy_without_flag():
    """Test that forbid policies raise error without handle_forbids=True."""
    policy = (
        'forbid(principal == User::"alice", action == Action::"s3:DeleteObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    with pytest.raises(ValueError, match="forbid policies are not yet fully supported"):
        compile_policies([policy], handle_forbids=False)


def test_forbid_excludes_permit_scope():
    """Test that forbid policies exclude matching permit scopes."""
    policies = [
        # Permit read and write
        (
            'permit(principal == User::"alice", action in [Action::"s3:GetObject", '
            'Action::"s3:PutObject", Action::"s3:DeleteObject"], '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        # Forbid delete
        (
            'forbid(principal == User::"alice", action == Action::"s3:DeleteObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
    ]
    result = compile_policies(policies, handle_forbids=True)

    # Should have read and write, but not delete
    assert "alice" in result
    assert "S3Object:analytics-data/report.csv:s3:GetObject" in result["alice"]
    assert "S3Object:analytics-data/report.csv:s3:PutObject" in result["alice"]
    assert "S3Object:analytics-data/report.csv:s3:DeleteObject" not in result["alice"]


def test_forbid_all_scopes_removes_principal():
    """Test that forbidding all permits removes principal from result."""
    policies = [
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        (
            'forbid(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
    ]
    result = compile_policies(policies, handle_forbids=True)

    # All scopes forbidden, so principal should not appear
    assert "alice" not in result


def test_multiple_principals_with_forbids():
    """Test forbid handling with multiple principals."""
    policies = [
        # Alice: permit read
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        # Bob: permit read and write
        (
            'permit(principal == User::"bob", action in [Action::"s3:GetObject", '
            'Action::"s3:PutObject"], resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        # Bob: forbid write
        (
            'forbid(principal == User::"bob", action == Action::"s3:PutObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
    ]
    result = compile_policies(policies, handle_forbids=True)

    # Alice should have read
    assert "alice" in result
    assert "S3Object:analytics-data/report.csv:s3:GetObject" in result["alice"]

    # Bob should have read, but not write
    assert "bob" in result
    assert "S3Object:analytics-data/report.csv:s3:GetObject" in result["bob"]
    assert "S3Object:analytics-data/report.csv:s3:PutObject" not in result["bob"]


def test_forbid_different_bucket():
    """Test that forbid only affects matching bucket."""
    policies = [
        # Permit in two buckets
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" || resource in S3Bucket::"raw-data" };'
        ),
        # Forbid only in analytics-data
        (
            'forbid(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
    ]
    result = compile_policies(policies, handle_forbids=True)

    # Should still have access to raw-data bucket
    assert "alice" in result
    assert "S3Object:raw-data/report.csv:s3:GetObject" in result["alice"]
    assert "S3Object:analytics-data/report.csv:s3:GetObject" not in result["alice"]


def test_forbid_precedence_over_permit():
    """Test that forbid takes precedence even when permit is defined after."""
    # Define permit first, then forbid
    policies_forbid_last = [
        (
            'permit(principal == User::"alice", action == Action::"s3:DeleteObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        (
            'forbid(principal == User::"alice", action == Action::"s3:DeleteObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
    ]

    # Define forbid first, then permit
    policies_permit_last = [
        (
            'forbid(principal == User::"alice", action == Action::"s3:DeleteObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        (
            'permit(principal == User::"alice", action == Action::"s3:DeleteObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
    ]

    # Both should have same result: forbid wins
    result1 = compile_policies(policies_forbid_last, handle_forbids=True)
    result2 = compile_policies(policies_permit_last, handle_forbids=True)

    assert "alice" not in result1  # All scopes forbidden
    assert "alice" not in result2  # All scopes forbidden


def test_forbid_bucket_level():
    """Test forbid at bucket level."""
    policies = [
        # Permit list bucket
        (
            'permit(principal == User::"alice", action == Action::"s3:ListBucket", '
            'resource == S3Bucket::"analytics-data");'
        ),
        # Forbid list bucket
        (
            'forbid(principal == User::"alice", action == Action::"s3:ListBucket", '
            'resource == S3Bucket::"analytics-data");'
        ),
    ]
    result = compile_policies(policies, handle_forbids=True)

    # ListBucket should be forbidden
    assert "alice" not in result
