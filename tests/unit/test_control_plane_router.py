"""Unit tests for control plane router endpoints."""

from __future__ import annotations

from unittest import mock
from unittest.mock import MagicMock, Mock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from raja.server.routers import control_plane


def _make_request(request_id: str | None = None) -> Request:
    """Create a mock Request object."""
    headers = []
    if request_id:
        headers.append((b"x-request-id", request_id.encode()))
    scope = {"type": "http", "headers": headers}
    return Request(scope)


def test_issue_token_raja_type():
    """Test issuing a RAJA token."""
    table = MagicMock()
    table.get_item.return_value = {"Item": {"scopes": ["Document:doc1:read"]}}
    audit_table = MagicMock()

    payload = control_plane.TokenRequest(principal="alice", token_type="raja")
    response = control_plane.issue_token(
        _make_request(),
        payload,
        table=table,
        audit_table=audit_table,
        secret="secret",
    )

    assert response["principal"] == "alice"
    assert "token" in response
    assert "scopes" in response
    assert response["scopes"] == ["Document:doc1:read"]


def test_issue_token_rajee_type():
    """Test issuing a RAJEE token with scopes."""
    table = MagicMock()
    table.get_item.return_value = {"Item": {"scopes": ["S3Object:bucket:key:s3:GetObject"]}}
    audit_table = MagicMock()

    # Create a mock request with base_url
    request = MagicMock()
    request.headers = MagicMock()
    request.headers.get = Mock(return_value=None)
    base_url_mock = Mock()
    base_url_mock.__str__ = Mock(return_value="https://api.example.com/")
    request.base_url = base_url_mock

    payload = control_plane.TokenRequest(principal="alice", token_type="rajee")
    response = control_plane.issue_token(
        request,
        payload,
        table=table,
        audit_table=audit_table,
        secret="secret",
    )

    assert response["principal"] == "alice"
    assert "token" in response
    assert "scopes" in response


def test_issue_token_invalid_type():
    """Test that issuing a token with invalid type raises HTTPException."""
    table = MagicMock()
    table.get_item.return_value = {"Item": {"scopes": ["Document:doc1:read"]}}
    audit_table = MagicMock()

    payload = control_plane.TokenRequest(principal="alice", token_type="invalid")
    with pytest.raises(HTTPException) as exc_info:
        control_plane.issue_token(
            _make_request(),
            payload,
            table=table,
            audit_table=audit_table,
            secret="secret",
        )

    assert exc_info.value.status_code == 400
    assert "Unsupported token_type" in exc_info.value.detail


def test_issue_token_audit_failure():
    """Test that audit log failures don't prevent token issuance."""
    table = MagicMock()
    table.get_item.return_value = {"Item": {"scopes": ["Document:doc1:read"]}}
    audit_table = MagicMock()
    audit_table.put_item.side_effect = Exception("DynamoDB error")

    payload = control_plane.TokenRequest(principal="alice")
    response = control_plane.issue_token(
        _make_request(),
        payload,
        table=table,
        audit_table=audit_table,
        secret="secret",
    )

    # Should still succeed despite audit failure
    assert response["principal"] == "alice"
    assert "token" in response


def test_list_principals_with_limit():
    """Test listing principals with a limit."""
    table = MagicMock()
    table.scan.return_value = {
        "Items": [{"principal": "alice"}, {"principal": "bob"}],
    }

    response = control_plane.list_principals(limit=10, table=table)

    assert len(response["principals"]) == 2
    table.scan.assert_called_once_with(Limit=10)


def test_list_principals_without_limit():
    """Test listing principals without a limit."""
    table = MagicMock()
    table.scan.return_value = {
        "Items": [{"principal": "alice"}],
    }

    response = control_plane.list_principals(limit=None, table=table)

    assert len(response["principals"]) == 1
    table.scan.assert_called_once_with()


def test_create_principal():
    """Test creating a principal."""
    table = MagicMock()

    request = control_plane.PrincipalRequest(
        principal="alice", scopes=["Document:doc1:read", "Document:doc2:write"]
    )
    response = control_plane.create_principal(request, table=table)

    assert response["principal"] == "alice"
    assert response["scopes"] == ["Document:doc1:read", "Document:doc2:write"]
    table.put_item.assert_called_once()


def test_create_principal_empty_scopes():
    """Test creating a principal with no scopes."""
    table = MagicMock()

    request = control_plane.PrincipalRequest(principal="alice", scopes=[])
    response = control_plane.create_principal(request, table=table)

    assert response["principal"] == "alice"
    assert response["scopes"] == []


def test_delete_principal():
    """Test deleting a principal."""
    table = MagicMock()

    response = control_plane.delete_principal("alice", table=table)

    assert "deleted" in response["message"]
    table.delete_item.assert_called_once_with(Key={"principal": "alice"})


def test_list_policies_without_statements():
    """Test listing policies without statements."""
    control_plane.POLICY_STORE_ID = "store-123"
    avp = MagicMock()
    avp.list_policies.return_value = {
        "policies": [{"policyId": "p1"}, {"policyId": "p2"}],
    }

    response = control_plane.list_policies(include_statements=False, avp=avp)

    assert len(response["policies"]) == 2
    avp.get_policy.assert_not_called()


def test_list_policies_with_statements():
    """Test listing policies with statements included."""
    control_plane.POLICY_STORE_ID = "store-123"
    avp = MagicMock()
    avp.list_policies.return_value = {
        "policies": [{"policyId": "p1"}],
    }
    avp.get_policy.return_value = {
        "definition": {"static": {"statement": "permit(...);"}},
    }

    response = control_plane.list_policies(include_statements=True, avp=avp)

    assert len(response["policies"]) == 1
    assert response["policies"][0]["policyId"] == "p1"
    assert "definition" in response["policies"][0]
    avp.get_policy.assert_called_once()


def test_list_policies_skips_missing_policy_id():
    """Test that list_policies skips policies without policyId."""
    control_plane.POLICY_STORE_ID = "store-123"
    avp = MagicMock()
    avp.list_policies.return_value = {
        "policies": [{"policyId": "p1"}, {}],  # Second policy missing policyId
    }
    avp.get_policy.return_value = {
        "definition": {"static": {"statement": "permit(...);"}},
    }

    response = control_plane.list_policies(include_statements=True, avp=avp)

    assert len(response["policies"]) == 1
    avp.get_policy.assert_called_once()


def test_get_jwks():
    """Test JWKS endpoint returns correct format."""
    response = control_plane.get_jwks(secret="test-secret")

    assert "keys" in response
    assert len(response["keys"]) == 1
    key = response["keys"][0]
    assert key["kty"] == "oct"
    assert key["kid"] == "raja-jwt-key"
    assert key["alg"] == "HS256"
    assert "k" in key


def test_compile_policies_missing_statement():
    """Test that compile_policies skips policies without statements."""
    control_plane.POLICY_STORE_ID = "store-123"
    avp = MagicMock()
    avp.list_policies.return_value = {
        "policies": [{"policyId": "p1"}, {"policyId": "p2"}],
    }
    avp.get_policy.side_effect = [
        {"definition": {"static": {"statement": ""}}},  # Empty statement
        {
            "definition": {
                "static": {
                    "statement": (
                        'permit(principal == User::"alice", '
                        'action == Action::"read", '
                        'resource == Document::"doc1");'
                    )
                }
            }
        },
    ]
    mappings_table = MagicMock()
    principal_table = MagicMock()
    audit_table = MagicMock()

    response = control_plane.compile_policies(
        _make_request(),
        avp=avp,
        mappings_table=mappings_table,
        principal_table=principal_table,
        audit_table=audit_table,
    )

    # Should only compile the valid policy
    assert response["policies_compiled"] == 1


def test_compile_policies_handles_compilation_error():
    """Test that compile_policies continues on compilation errors."""
    control_plane.POLICY_STORE_ID = "store-123"
    avp = MagicMock()
    avp.list_policies.return_value = {
        "policies": [{"policyId": "p1"}, {"policyId": "p2"}],
    }
    avp.get_policy.side_effect = [
        {"definition": {"static": {"statement": "invalid policy syntax"}}},
        {
            "definition": {
                "static": {
                    "statement": (
                        'permit(principal == User::"alice", '
                        'action == Action::"read", '
                        'resource == Document::"doc1");'
                    )
                }
            }
        },
    ]
    mappings_table = MagicMock()
    principal_table = MagicMock()
    audit_table = MagicMock()

    response = control_plane.compile_policies(
        _make_request(),
        avp=avp,
        mappings_table=mappings_table,
        principal_table=principal_table,
        audit_table=audit_table,
    )

    # Should compile the valid policy despite error in first one
    assert response["policies_compiled"] == 1


def test_compile_policies_audit_failure():
    """Test that compile_policies continues despite audit failures."""
    control_plane.POLICY_STORE_ID = "store-123"
    avp = MagicMock()
    avp.list_policies.return_value = {"policies": [{"policyId": "p1"}]}
    avp.get_policy.return_value = {
        "definition": {
            "static": {
                "statement": (
                    'permit(principal == User::"alice", '
                    'action == Action::"read", '
                    'resource == Document::"doc1");'
                )
            }
        }
    }
    mappings_table = MagicMock()
    principal_table = MagicMock()
    audit_table = MagicMock()
    audit_table.put_item.side_effect = Exception("Audit write failed")

    response = control_plane.compile_policies(
        _make_request(),
        avp=avp,
        mappings_table=mappings_table,
        principal_table=principal_table,
        audit_table=audit_table,
    )

    # Should succeed despite audit failure
    assert response["policies_compiled"] == 1


def test_require_env_raises_when_missing():
    """Test that _require_env raises RuntimeError when value is missing."""
    with pytest.raises(RuntimeError, match="TEST_VAR is required"):
        control_plane._require_env(None, "TEST_VAR")


def test_require_env_returns_value():
    """Test that _require_env returns value when present."""
    result = control_plane._require_env("test-value", "TEST_VAR")
    assert result == "test-value"


def test_get_request_id_from_x_request_id():
    """Test that _get_request_id extracts from x-request-id header."""
    request = _make_request(request_id="req-123")
    request_id = control_plane._get_request_id(request)
    assert request_id == "req-123"


def test_get_request_id_generates_uuid():
    """Test that _get_request_id generates UUID when no header present."""
    request = _make_request()
    request_id = control_plane._get_request_id(request)
    # Should be a valid UUID-like string
    assert len(request_id) > 0
    assert "-" in request_id
