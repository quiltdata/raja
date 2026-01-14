import pytest

from raja import create_token, decode_token


@pytest.mark.hypothesis
def test_transparency_token_reveals_scopes():
    scopes = ["Document:doc1:read", "Document:doc1:write"]
    token = create_token("alice", scopes, ttl=300, secret="secret")
    payload = decode_token(token)
    assert payload["scopes"] == scopes
