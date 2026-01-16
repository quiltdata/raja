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
