from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from raja.server.routers import control_plane


def _make_request() -> Request:
    scope = {"type": "http", "headers": []}
    return Request(scope)


def test_issue_token_writes_audit_on_success() -> None:
    table = MagicMock()
    table.get_item.return_value = {"Item": {"scopes": ["S3Object:bucket:key:s3:GetObject"]}}
    audit_table = MagicMock()

    payload = control_plane.TokenRequest(principal="alice")
    response = control_plane.issue_token(
        _make_request(),
        payload,
        table=table,
        audit_table=audit_table,
        secret="secret",
    )

    assert response["principal"] == "alice"
    audit_table.put_item.assert_called()
    item = audit_table.put_item.call_args.kwargs["Item"]
    assert item["decision"] == "SUCCESS"


def test_issue_token_writes_audit_on_missing_principal() -> None:
    table = MagicMock()
    table.get_item.return_value = {}
    audit_table = MagicMock()

    payload = control_plane.TokenRequest(principal="missing")
    with pytest.raises(HTTPException):
        control_plane.issue_token(
            _make_request(),
            payload,
            table=table,
            audit_table=audit_table,
            secret="secret",
        )

    audit_table.put_item.assert_called()
    item = audit_table.put_item.call_args.kwargs["Item"]
    assert item["decision"] == "DENY"
