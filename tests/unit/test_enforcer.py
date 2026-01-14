from raja.enforcer import enforce
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
