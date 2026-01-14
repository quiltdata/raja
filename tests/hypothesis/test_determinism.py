import pytest

from raja import AuthRequest, create_token, enforce


@pytest.mark.hypothesis
def test_determinism_same_input_same_decision():
    secret = "secret"
    token = create_token("alice", ["Document:doc1:read"], ttl=300, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")

    decisions = [enforce(token, request, secret) for _ in range(100)]
    assert all(decision.allowed for decision in decisions)
    assert {decision.reason for decision in decisions} == {"scope matched"}
