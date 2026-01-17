import pytest

from raja.exceptions import ScopeParseError, ScopeValidationError
from raja.models import Scope
from raja.scope import format_scope, is_subset, parse_scope


def test_parse_scope_valid():
    scope = parse_scope("Document:doc123:read")
    assert scope.resource_type == "Document"
    assert scope.resource_id == "doc123"
    assert scope.action == "read"


def test_parse_scope_invalid():
    with pytest.raises(ScopeParseError):
        parse_scope("Document-doc123-read")


def test_parse_scope_missing_parts():
    """Test that scopes with missing parts are rejected."""
    with pytest.raises(ScopeParseError):
        parse_scope("Document:doc123")


def test_parse_scope_empty_string():
    """Test that empty scope strings are rejected."""
    with pytest.raises(ScopeParseError):
        parse_scope("")


def test_parse_scope_with_colons_in_action():
    """Test that colons in action part are preserved."""
    scope = parse_scope("Document:doc123:read:write")
    assert scope.resource_type == "Document"
    assert scope.resource_id == "doc123"
    assert scope.action == "read:write"


def test_format_scope():
    assert format_scope("Document", "doc123", "read") == "Document:doc123:read"


def test_is_subset_with_scopes():
    requested = Scope(resource_type="Document", resource_id="doc123", action="read")
    granted = [Scope(resource_type="Document", resource_id="doc123", action="read")]
    assert is_subset(requested, granted) is True


def test_is_subset_with_strings():
    requested = Scope(resource_type="Document", resource_id="doc123", action="read")
    granted = ["Document:doc123:read", "Document:doc456:write"]
    assert is_subset(requested, granted) is True


def test_is_subset_not_granted():
    """Test that is_subset returns False when scope is not granted."""
    requested = Scope(resource_type="Document", resource_id="doc123", action="write")
    granted = ["Document:doc123:read"]
    assert is_subset(requested, granted) is False


def test_is_subset_empty_granted():
    """Test that is_subset returns False with empty granted scopes."""
    requested = Scope(resource_type="Document", resource_id="doc123", action="read")
    granted = []
    assert is_subset(requested, granted) is False


def test_is_subset_mixed_types():
    """Test that is_subset works with mixed Scope objects and strings."""
    requested = Scope(resource_type="Document", resource_id="doc123", action="read")
    granted = [
        Scope(resource_type="Document", resource_id="doc123", action="read"),
        "Document:doc456:write",
    ]
    assert is_subset(requested, granted) is True


def test_is_subset_invalid_granted_scope_string():
    """Test that is_subset raises error for invalid granted scope strings."""
    requested = Scope(resource_type="Document", resource_id="doc123", action="read")
    granted = ["invalid-scope"]
    with pytest.raises(ScopeParseError):
        is_subset(requested, granted)


def test_is_subset_with_duplicate_scopes():
    """Test that is_subset normalizes duplicate scopes."""
    requested = Scope(resource_type="Document", resource_id="doc123", action="read")
    granted = [
        "Document:doc123:read",
        "Document:doc123:read",  # Duplicate
        "Document:doc456:write",
    ]
    assert is_subset(requested, granted) is True


def test_format_scope_with_special_characters():
    """Test that format_scope handles special characters in components."""
    scope_str = format_scope("S3Object", "bucket/key.txt", "s3:GetObject")
    assert scope_str == "S3Object:bucket/key.txt:s3:GetObject"
