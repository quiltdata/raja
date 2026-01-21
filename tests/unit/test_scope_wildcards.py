"""Tests for wildcard pattern matching and scope expansion (Phase 4)."""

import pytest

from raja.scope import (
    expand_wildcard_scope,
    filter_scopes_by_pattern,
    matches_pattern,
    parse_scope,
    scope_matches,
)


def test_matches_pattern_exact():
    """Test exact pattern matching."""
    assert matches_pattern("value", "value")
    assert not matches_pattern("value", "other")


def test_matches_pattern_wildcard():
    """Test wildcard pattern matching."""
    assert matches_pattern("anything", "*")
    assert matches_pattern("", "*")
    assert matches_pattern("s3:GetObject", "*")


def test_matches_pattern_prefix():
    """Test prefix pattern matching."""
    assert matches_pattern("s3:GetObject", "s3:*")
    assert matches_pattern("s3:PutObject", "s3:*")
    assert not matches_pattern("dynamodb:GetItem", "s3:*")


def test_matches_pattern_suffix():
    """Test suffix pattern matching."""
    assert matches_pattern("doc:read", "*:read")
    assert matches_pattern("file:read", "*:read")
    assert not matches_pattern("doc:write", "*:read")


def test_matches_pattern_middle():
    """Test pattern with wildcard in middle."""
    assert matches_pattern("s3:GetObject:v1", "s3:*:v1")
    assert matches_pattern("s3:PutObject:v1", "s3:*:v1")
    assert not matches_pattern("s3:GetObject:v2", "s3:*:v1")


def test_scope_matches_exact():
    """Test exact scope matching."""
    requested = parse_scope("Document:doc123:read")
    granted = parse_scope("Document:doc123:read")
    assert scope_matches(requested, granted)


def test_scope_matches_resource_wildcard():
    """Test scope matching with resource ID wildcard."""
    requested = parse_scope("Document:doc123:read")
    granted = parse_scope("Document:*:read")
    assert scope_matches(requested, granted)


def test_scope_matches_action_wildcard():
    """Test scope matching with action wildcard."""
    requested = parse_scope("Document:doc123:read")
    granted = parse_scope("Document:doc123:*")
    assert scope_matches(requested, granted)


def test_scope_matches_resource_type_wildcard():
    """Test scope matching with resource type wildcard."""
    requested = parse_scope("Document:doc123:read")
    granted = parse_scope("*:doc123:read")
    assert scope_matches(requested, granted)


def test_scope_matches_full_wildcard():
    """Test scope matching with full wildcard."""
    requested = parse_scope("Document:doc123:read")
    granted = parse_scope("*:*:*")
    assert scope_matches(requested, granted)


def test_scope_matches_action_prefix():
    """Test scope matching with action prefix wildcard."""
    requested = parse_scope("S3Object:obj123:s3:GetObject")
    granted = parse_scope("S3Object:obj123:s3:*")
    assert scope_matches(requested, granted)


def test_scope_not_matches_different_resource():
    """Test scope doesn't match different resource."""
    requested = parse_scope("Document:doc123:read")
    granted = parse_scope("Document:doc456:read")
    assert not scope_matches(requested, granted)


def test_scope_not_matches_different_action():
    """Test scope doesn't match different action."""
    requested = parse_scope("Document:doc123:write")
    granted = parse_scope("Document:doc123:read")
    assert not scope_matches(requested, granted)


def test_expand_wildcard_scope_no_wildcards():
    """Test expanding scope without wildcards returns as-is."""
    result = expand_wildcard_scope("Document:doc123:read")
    assert result == ["Document:doc123:read"]


def test_expand_wildcard_scope_resource_id_wildcard():
    """Test expanding resource ID wildcard returns as-is (runtime expansion)."""
    result = expand_wildcard_scope("Document:*:read")
    assert result == ["Document:*:read"]


def test_expand_wildcard_scope_resource_type():
    """Test expanding resource type wildcard with context."""
    result = expand_wildcard_scope("*:doc123:read", resource_types=["Document", "File", "Image"])
    assert len(result) == 3
    assert "Document:doc123:read" in result
    assert "File:doc123:read" in result
    assert "Image:doc123:read" in result


def test_expand_wildcard_scope_resource_type_no_context():
    """Test expanding resource type wildcard without context raises error."""
    with pytest.raises(ValueError, match="cannot expand resource type wildcard"):
        expand_wildcard_scope("*:doc123:read")


def test_expand_wildcard_scope_action():
    """Test expanding action wildcard with context."""
    result = expand_wildcard_scope(
        "Document:doc123:s3:*", actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    )
    assert len(result) == 3
    assert "Document:doc123:s3:GetObject" in result
    assert "Document:doc123:s3:PutObject" in result
    assert "Document:doc123:s3:DeleteObject" in result


def test_expand_wildcard_scope_action_no_context():
    """Test expanding action wildcard without context returns as-is."""
    result = expand_wildcard_scope("Document:doc123:s3:*")
    assert result == ["Document:doc123:s3:*"]


def test_expand_wildcard_scope_action_prefix():
    """Test expanding action prefix wildcard."""
    result = expand_wildcard_scope(
        "Document:doc123:s3:Get*",
        actions=["s3:GetObject", "s3:GetObjectAcl", "s3:PutObject"],
    )
    assert len(result) == 2
    assert "Document:doc123:s3:GetObject" in result
    assert "Document:doc123:s3:GetObjectAcl" in result
    assert "Document:doc123:s3:PutObject" not in result


def test_filter_scopes_no_patterns():
    """Test filtering with no patterns returns all scopes."""
    scopes = ["S3Bucket:a:read", "S3Bucket:b:read", "S3Bucket:c:write"]
    result = filter_scopes_by_pattern(scopes)
    assert result == scopes


def test_filter_scopes_include_pattern():
    """Test filtering with inclusion pattern."""
    scopes = ["S3Bucket:a:read", "S3Bucket:b:read", "S3Bucket:c:write"]
    result = filter_scopes_by_pattern(scopes, include_patterns=["*:*:read"])
    assert len(result) == 2
    assert "S3Bucket:a:read" in result
    assert "S3Bucket:b:read" in result
    assert "S3Bucket:c:write" not in result


def test_filter_scopes_exclude_pattern():
    """Test filtering with exclusion pattern."""
    scopes = ["S3Bucket:a:read", "S3Bucket:b:read", "S3Bucket:a:write"]
    result = filter_scopes_by_pattern(scopes, exclude_patterns=["*:a:write"])
    assert len(result) == 2
    assert "S3Bucket:a:read" in result
    assert "S3Bucket:b:read" in result
    assert "S3Bucket:a:write" not in result


def test_filter_scopes_include_and_exclude():
    """Test filtering with both inclusion and exclusion patterns."""
    scopes = [
        "S3Bucket:a:s3:GetObject",
        "S3Bucket:a:s3:PutObject",
        "S3Bucket:b:s3:GetObject",
        "Document:doc1:read",
    ]
    result = filter_scopes_by_pattern(
        scopes, include_patterns=["S3Bucket:*:*"], exclude_patterns=["*:*:s3:PutObject"]
    )
    assert len(result) == 2
    assert "S3Bucket:a:s3:GetObject" in result
    assert "S3Bucket:b:s3:GetObject" in result
    assert "S3Bucket:a:s3:PutObject" not in result
    assert "Document:doc1:read" not in result


def test_filter_scopes_wildcard_bucket():
    """Test filtering S3 bucket scopes with wildcards."""
    scopes = [
        "S3Object:bucket-a/key1:s3:GetObject",
        "S3Object:bucket-b/key1:s3:GetObject",
        "S3Object:bucket-a/key2:s3:PutObject",
    ]
    result = filter_scopes_by_pattern(scopes, include_patterns=["S3Object:bucket-a/*:*"])
    assert len(result) == 2
    assert "S3Object:bucket-a/key1:s3:GetObject" in result
    assert "S3Object:bucket-a/key2:s3:PutObject" in result
    assert "S3Object:bucket-b/key1:s3:GetObject" not in result
