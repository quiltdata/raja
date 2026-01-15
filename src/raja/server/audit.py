"""Audit log helpers for control plane actions."""

from __future__ import annotations

import base64
import json
import time
import uuid
from typing import Any, cast

from boto3.dynamodb.conditions import Attr, Key

AUDIT_PARTITION_KEY = "AUDIT"
DEFAULT_TTL_DAYS = 30


def build_audit_item(
    *,
    principal: str,
    action: str,
    resource: str,
    decision: str,
    policy_store_id: str | None,
    request_id: str,
    ttl_days: int = DEFAULT_TTL_DAYS,
) -> dict[str, Any]:
    timestamp = int(time.time())
    event_id = f"{timestamp}-{uuid.uuid4()}"
    ttl = timestamp + ttl_days * 86400
    return {
        "pk": AUDIT_PARTITION_KEY,
        "event_id": event_id,
        "timestamp": timestamp,
        "principal": principal,
        "action": action,
        "resource": resource,
        "decision": decision,
        "policy_store_id": policy_store_id,
        "request_id": request_id,
        "ttl": ttl,
    }


def encode_pagination_token(last_evaluated_key: dict[str, Any] | None) -> str | None:
    if not last_evaluated_key:
        return None
    raw = json.dumps(last_evaluated_key).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8")


def decode_pagination_token(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    raw = base64.urlsafe_b64decode(token.encode("utf-8"))
    return cast(dict[str, Any], json.loads(raw.decode("utf-8")))


def query_audit_entries(
    *,
    table: Any,
    limit: int,
    next_token: str | None,
    principal: str | None,
    action: str | None,
    resource: str | None,
    start_time: int | None,
    end_time: int | None,
) -> tuple[list[dict[str, Any]], str | None]:
    query_kwargs: dict[str, Any] = {
        "KeyConditionExpression": Key("pk").eq(AUDIT_PARTITION_KEY),
        "Limit": limit,
        "ScanIndexForward": False,
    }

    exclusive_start_key = decode_pagination_token(next_token)
    if exclusive_start_key:
        query_kwargs["ExclusiveStartKey"] = exclusive_start_key

    filter_expression: Any | None = None
    if principal:
        filter_expression = Attr("principal").eq(principal)
    if action:
        filter_expression = (
            filter_expression & Attr("action").eq(action)
            if filter_expression is not None
            else Attr("action").eq(action)
        )
    if resource:
        filter_expression = (
            filter_expression & Attr("resource").eq(resource)
            if filter_expression is not None
            else Attr("resource").eq(resource)
        )
    if start_time is not None:
        filter_expression = (
            filter_expression & Attr("timestamp").gte(start_time)
            if filter_expression is not None
            else Attr("timestamp").gte(start_time)
        )
    if end_time is not None:
        filter_expression = (
            filter_expression & Attr("timestamp").lte(end_time)
            if filter_expression is not None
            else Attr("timestamp").lte(end_time)
        )

    if filter_expression is not None:
        query_kwargs["FilterExpression"] = filter_expression

    response = table.query(**query_kwargs)
    entries = response.get("Items", [])
    token = encode_pagination_token(response.get("LastEvaluatedKey"))
    return entries, token
