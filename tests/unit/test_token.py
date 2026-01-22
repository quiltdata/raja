import time

import jwt
import pytest

from raja.exceptions import TokenExpiredError, TokenInvalidError, TokenValidationError
from raja.models import Token
from raja.token import (
    create_token,
    create_token_with_grants,
    create_token_with_package_grant,
    create_token_with_package_map,
    decode_token,
    is_expired,
    validate_package_map_token,
    validate_package_token,
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


def test_create_token_with_package_grant_includes_claims():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        ttl=60,
        secret="secret",
        issuer="https://issuer.test",
        audience=["raja"],
    )
    payload = decode_token(token_str)
    assert payload["sub"] == "alice"
    assert payload["quilt_uri"] == quilt_uri
    assert payload["mode"] == "read"
    assert payload["iss"] == "https://issuer.test"
    assert payload["aud"] == ["raja"]


def test_validate_package_token_returns_model():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        ttl=60,
        secret="secret",
    )
    token = validate_package_token(token_str, "secret")
    assert token.subject == "alice"
    assert token.quilt_uri == quilt_uri
    assert token.mode == "read"


def test_create_token_with_package_map_includes_claims():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=60,
        secret="secret",
        issuer="https://issuer.test",
        audience=["raja"],
    )
    payload = decode_token(token_str)
    assert payload["sub"] == "alice"
    assert payload["quilt_uri"] == quilt_uri
    assert payload["mode"] == "read"
    assert payload["logical_bucket"] == "logical-bucket"
    assert payload["logical_key"] == "logical/file.csv"
    assert payload["iss"] == "https://issuer.test"
    assert payload["aud"] == ["raja"]


def test_create_token_with_package_grant_rejects_write_mode():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    with pytest.raises(ValueError):
        create_token_with_package_grant(
            "alice",
            quilt_uri=quilt_uri,
            mode="readwrite",
            ttl=60,
            secret="secret",
        )


def test_create_token_with_package_map_rejects_write_mode():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    with pytest.raises(ValueError):
        create_token_with_package_map(
            "alice",
            quilt_uri=quilt_uri,
            mode="readwrite",
            logical_bucket="logical-bucket",
            logical_key="logical/file.csv",
            ttl=60,
            secret="secret",
        )


def test_validate_package_map_token_returns_model():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=60,
        secret="secret",
    )
    token = validate_package_map_token(token_str, "secret")
    assert token.subject == "alice"
    assert token.quilt_uri == quilt_uri
    assert token.mode == "read"
    assert token.logical_bucket == "logical-bucket"
    assert token.logical_key == "logical/file.csv"


def test_validate_package_map_token_rejects_missing_logical_claims():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = jwt.encode(
        {"sub": "alice", "quilt_uri": quilt_uri, "mode": "read"},
        "secret",
        algorithm="HS256",
    )
    with pytest.raises(TokenValidationError):
        validate_package_map_token(token_str, "secret")


def test_validate_package_map_token_rejects_conflicting_logical_path():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = jwt.encode(
        {
            "sub": "alice",
            "quilt_uri": quilt_uri,
            "mode": "read",
            "logical_bucket": "bucket-a",
            "logical_key": "path/file.csv",
            "logical_s3_path": "s3://bucket-b/other.csv",
        },
        "secret",
        algorithm="HS256",
    )
    with pytest.raises(TokenValidationError):
        validate_package_map_token(token_str, "secret")


def test_validate_package_map_token_rejects_invalid_logical_path():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = jwt.encode(
        {
            "sub": "alice",
            "quilt_uri": quilt_uri,
            "mode": "read",
            "logical_s3_path": "not-a-path",
        },
        "secret",
        algorithm="HS256",
    )
    with pytest.raises(TokenValidationError):
        validate_package_map_token(token_str, "secret")


def test_validate_package_token_rejects_missing_quilt_uri():
    token_str = jwt.encode({"sub": "alice", "mode": "read"}, "secret", algorithm="HS256")
    with pytest.raises(TokenValidationError):
        validate_package_token(token_str, "secret")


def test_validate_package_token_rejects_invalid_mode():
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = jwt.encode(
        {"sub": "alice", "quilt_uri": quilt_uri, "mode": "readwrite"},
        "secret",
        algorithm="HS256",
    )
    with pytest.raises(TokenValidationError):
        validate_package_token(token_str, "secret")


def test_validate_token_rejects_missing_subject():
    """Test that validate_token rejects tokens missing a subject."""
    token_str = jwt.encode({"scopes": ["Document:doc1:read"]}, "secret", algorithm="HS256")
    with pytest.raises(TokenValidationError):
        validate_token(token_str, "secret")


def test_validate_token_rejects_null_scopes():
    """Test that validate_token rejects tokens with null scopes."""
    token_str = jwt.encode({"sub": "alice", "scopes": None}, "secret", algorithm="HS256")
    with pytest.raises(TokenValidationError):
        validate_token(token_str, "secret")


def test_validate_token_rejects_non_list_scopes():
    """Test that validate_token rejects tokens with non-list scopes."""
    token_str = jwt.encode(
        {"sub": "alice", "scopes": "Document:doc1:read"}, "secret", algorithm="HS256"
    )
    with pytest.raises(TokenValidationError):
        validate_token(token_str, "secret")


def test_validate_token_large_scopes():
    scopes = [f"Document:doc{i}:read" for i in range(1000)]
    token_str = create_token("alice", scopes, ttl=60, secret="secret")
    token = validate_token(token_str, "secret")
    assert len(token.scopes) == 1000
