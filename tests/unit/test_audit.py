from __future__ import annotations

from unittest.mock import MagicMock

from raja.server import audit


def test_build_audit_item_includes_fields() -> None:
    item = audit.build_audit_item(
        principal="alice",
        action="token.issue",
        resource="alice",
        decision="SUCCESS",
        policy_store_id="store",
        request_id="req",
        ttl_days=1,
    )
    assert item["principal"] == "alice"
    assert item["action"] == "token.issue"
    assert item["resource"] == "alice"
    assert item["decision"] == "SUCCESS"
    assert item["policy_store_id"] == "store"
    assert item["request_id"] == "req"
    assert item["ttl"] > item["timestamp"]


def test_pagination_token_round_trip() -> None:
    key = {"pk": "AUDIT", "event_id": "1"}
    token = audit.encode_pagination_token(key)
    assert token is not None
    decoded = audit.decode_pagination_token(token)
    assert decoded == key


def test_query_audit_entries_uses_next_token() -> None:
    table = MagicMock()
    table.query.return_value = {"Items": [], "LastEvaluatedKey": {"pk": "AUDIT", "event_id": "2"}}
    token = audit.encode_pagination_token({"pk": "AUDIT", "event_id": "1"})
    entries, next_token = audit.query_audit_entries(
        table=table,
        limit=10,
        next_token=token,
        principal="alice",
        action=None,
        resource=None,
        start_time=None,
        end_time=None,
    )
    assert entries == []
    assert next_token is not None
    assert table.query.called
