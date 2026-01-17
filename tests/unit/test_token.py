import time

import pytest

from raja.exceptions import TokenExpiredError, TokenInvalidError
from raja.models import Token
from raja.token import (
    create_token,
    create_token_with_grants,
    decode_token,
    is_expired,
    validate_token,
)


def test_create_and_validate_token():
    secret = "supersecret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    token = validate_token(token_str, secret)
    assert token.subject == "alice"
    assert "Document:doc1:read" in token.scopes


def test_decode_token_without_validation():
    secret = "supersecret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    payload = decode_token(token_str)
    assert payload["sub"] == "alice"


def test_validate_token_rejects_invalid_signature():
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret="secret-a")
    with pytest.raises(TokenInvalidError):
        validate_token(token_str, "secret-b")


def test_validate_token_rejects_expired():
    token_str = create_token("alice", ["Document:doc1:read"], ttl=-1, secret="secret")
    with pytest.raises(TokenExpiredError):
        validate_token(token_str, "secret")


def test_is_expired():
    token = Token(
        subject="alice",
        scopes=["Document:doc1:read"],
        issued_at=int(time.time()) - 10,
        expires_at=int(time.time()) - 1,
    )
    assert is_expired(token) is True


def test_is_not_expired():
    """Test that is_expired returns False for valid tokens."""
    token = Token(
        subject="alice",
        scopes=["Document:doc1:read"],
        issued_at=int(time.time()),
        expires_at=int(time.time()) + 3600,
    )
    assert is_expired(token) is False


def test_create_token_with_issuer():
    """Test that create_token includes issuer claim when provided."""
    token_str = create_token(
        "alice", ["Document:doc1:read"], ttl=60, secret="secret", issuer="https://issuer.test"
    )
    payload = decode_token(token_str)
    assert payload["iss"] == "https://issuer.test"


def test_create_token_with_audience_string():
    """Test that create_token includes audience claim as string."""
    token_str = create_token(
        "alice", ["Document:doc1:read"], ttl=60, secret="secret", audience="api-service"
    )
    payload = decode_token(token_str)
    assert payload["aud"] == "api-service"


def test_create_token_with_audience_list():
    """Test that create_token includes audience claim as list."""
    token_str = create_token(
        "alice",
        ["Document:doc1:read"],
        ttl=60,
        secret="secret",
        audience=["api-service", "web-app"],
    )
    payload = decode_token(token_str)
    assert payload["aud"] == ["api-service", "web-app"]


def test_create_token_with_grants_includes_claims():
    token_str = create_token_with_grants(
        "alice",
        ["s3:GetObject/bucket/key.txt"],
        ttl=60,
        secret="secret",
        issuer="https://issuer.test",
        audience=["raja-s3-proxy"],
    )
    payload = decode_token(token_str)
    assert payload["sub"] == "alice"
    assert payload["grants"] == ["s3:GetObject/bucket/key.txt"]
    assert payload["iss"] == "https://issuer.test"
    assert payload["aud"] == ["raja-s3-proxy"]


def test_decode_token_invalid_format():
    """Test that decode_token raises error for invalid token format."""
    with pytest.raises(TokenInvalidError):
        decode_token("not-a-valid-token")


def test_decode_token_empty_string():
    """Test that decode_token raises error for empty token."""
    with pytest.raises(TokenInvalidError):
        decode_token("")


def test_validate_token_malformed():
    """Test that validate_token raises error for malformed token."""
    with pytest.raises(TokenInvalidError):
        validate_token("malformed.token.here", "secret")


def test_create_token_with_grants_with_string_audience():
    """Test that create_token_with_grants properly handles string audience."""
    token_str = create_token_with_grants(
        "alice",
        ["grant1"],
        ttl=60,
        secret="secret",
        issuer=None,
        audience="service",
    )
    payload = decode_token(token_str)
    assert payload["aud"] == "service"
    assert payload["grants"] == ["grant1"]
    assert "iss" not in payload  # No issuer provided


def test_create_token_with_grants_without_issuer_audience():
    """Test that create_token_with_grants works without issuer/audience."""
    token_str = create_token_with_grants(
        "alice",
        ["grant1", "grant2"],
        ttl=60,
        secret="secret",
    )
    payload = decode_token(token_str)
    assert payload["grants"] == ["grant1", "grant2"]
    assert "iss" not in payload
    assert "aud" not in payload
