import pytest

from raja.models import Scope
from raja.scope import format_scope, is_subset, parse_scope


def test_parse_scope_valid():
    scope = parse_scope("Document:doc123:read")
    assert scope.resource_type == "Document"
    assert scope.resource_id == "doc123"
    assert scope.action == "read"


def test_parse_scope_invalid():
    with pytest.raises(ValueError):
        parse_scope("Document-doc123-read")


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
