import pytest

from raja.models import AuthRequest, CedarPolicy, Scope, Token


def test_scope_validation_rejects_empty():
    with pytest.raises(ValueError):
        Scope(resource_type="", resource_id="doc", action="read")


def test_scope_validation_rejects_colon():
    with pytest.raises(ValueError):
        Scope(resource_type="Document", resource_id="doc:1", action="read")


def test_auth_request_validation():
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    assert request.resource_type == "Document"


def test_token_requires_subject():
    with pytest.raises(ValueError):
        Token(subject=" ", scopes=["Document:doc1:read"], issued_at=1, expires_at=2)


def test_cedar_policy_effect_validation():
    with pytest.raises(ValueError):
        CedarPolicy(
            effect="allow",
            principal="User::\"alice\"",
            action="Action::\"read\"",
            resource="Document::\"doc1\"",
        )
