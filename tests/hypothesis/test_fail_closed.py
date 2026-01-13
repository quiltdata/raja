import pytest

from raja import AuthRequest, create_token, enforce


@pytest.mark.hypothesis
def test_fail_closed_unknown_request_denied():
    secret = "secret"
    token = create_token("alice", ["Document:doc1:read"], ttl=300, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="write")
    decision = enforce(token, request, secret)
    assert decision.allowed is False


@pytest.mark.hypothesis
def test_fail_closed_invalid_token_denied():
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce("bad-token", request, "secret")
    assert decision.allowed is False
