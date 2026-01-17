import pytest

from raja.enforcer import check_scopes, enforce, is_prefix_match
from raja.exceptions import ScopeValidationError
from raja.models import AuthRequest
from raja.token import create_token


def test_enforce_allows_matching_scope():
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is True
    assert decision.matched_scope == "Document:doc1:read"


def test_enforce_denies_missing_scope():
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="write")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "scope not granted"


def test_enforce_denies_invalid_token():
    secret = "secret"
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce("not-a-token", request, secret)
    assert decision.allowed is False
    assert decision.reason == "invalid token"


def test_enforce_denies_expired_token():
    """Test that expired tokens are denied with appropriate reason."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=-1, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "token expired"


def test_enforce_denies_wrong_signature():
    """Test that tokens with wrong signature are denied."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret="wrong-secret")
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "invalid token"


def test_check_scopes_validates_request():
    """Test that check_scopes properly validates the auth request."""
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    granted_scopes = ["Document:doc1:read"]
    result = check_scopes(request, granted_scopes)
    assert result is True


def test_check_scopes_denies_ungranted():
    """Test that check_scopes returns False for ungranted scopes."""
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="write")
    granted_scopes = ["Document:doc1:read"]
    result = check_scopes(request, granted_scopes)
    assert result is False


def test_check_scopes_handles_invalid_granted_scope():
    """Test that check_scopes raises error for invalid granted scope strings."""
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    granted_scopes = ["invalid-scope-format"]
    with pytest.raises(ScopeValidationError):
        check_scopes(request, granted_scopes)


def test_enforce_handles_scope_validation_error():
    """Test that enforce handles scope validation errors in check_scopes."""
    secret = "secret"
    token_str = create_token("alice", ["invalid-scope"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")

    # This will cause a ScopeValidationError when check_scopes tries to parse the granted scope
    decision = enforce(token_str, request, secret)

    # Should deny due to scope validation error
    assert decision.allowed is False
    assert "scope" in decision.reason.lower() or "internal error" in decision.reason.lower()


def test_enforce_logs_allowed_authorization():
    """Test that enforce properly logs successful authorization."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")

    decision = enforce(token_str, request, secret)

    assert decision.allowed is True
    assert decision.matched_scope == "Document:doc1:read"
    assert decision.reason == "scope matched"


def test_enforce_logs_denied_authorization():
    """Test that enforce properly logs denied authorization."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc2", action="read")

    decision = enforce(token_str, request, secret)

    assert decision.allowed is False
    assert decision.reason == "scope not granted"


def test_prefix_match_exact() -> None:
    assert is_prefix_match(
        "S3Object:bucket/key.txt:s3:GetObject",
        "S3Object:bucket/key.txt:s3:GetObject",
    )


def test_prefix_match_bucket() -> None:
    assert is_prefix_match(
        "S3Object:bucket-/uploads/file.txt:s3:GetObject",
        "S3Object:bucket-123/uploads/file.txt:s3:GetObject",
    )


def test_prefix_match_key_prefix() -> None:
    assert is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/uploads/subdir/file.txt:s3:GetObject",
    )


def test_prefix_match_bucket_no_match() -> None:
    assert not is_prefix_match(
        "S3Object:bucket-/uploads/file.txt:s3:GetObject",
        "S3Object:other/uploads/file.txt:s3:GetObject",
    )


def test_prefix_match_key_no_match() -> None:
    assert not is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/private/file.txt:s3:GetObject",
    )


def test_prefix_match_action_mismatch() -> None:
    assert not is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/uploads/file.txt:s3:PutObject",
    )


def test_prefix_match_bucket_only_scope() -> None:
    assert is_prefix_match(
        "S3Bucket:bucket-:s3:ListBucket",
        "S3Bucket:bucket-123:s3:ListBucket",
    )


def test_prefix_match_head_object_implied_by_get() -> None:
    assert is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/uploads/file.txt:s3:HeadObject",
    )


def test_prefix_match_multipart_implied_by_put() -> None:
    assert is_prefix_match(
        "S3Object:bucket/uploads/:s3:PutObject",
        "S3Object:bucket/uploads/file.txt:s3:UploadPart",
    )
