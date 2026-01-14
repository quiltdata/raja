"""Token introspection Lambda Handler.

Decodes JWT tokens without signature validation to reveal scopes.
"""

import json
from typing import Any

from raja import decode_token


def _extract_token(event: dict[str, Any]) -> str | None:
    query = event.get("queryStringParameters") or {}
    if isinstance(query, dict):
        token = query.get("token")
        if token:
            return token

    body = event.get("body", "{}")
    if isinstance(body, str):
        try:
            body = json.loads(body)
        except json.JSONDecodeError:
            return None

    if isinstance(body, dict):
        return body.get("token")

    return None


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Decode a JWT token and return its claims."""
    token = _extract_token(event)
    if not token:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Missing required field: token"}),
        }

    try:
        payload = decode_token(token)
    except Exception as exc:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": f"Invalid token: {exc}"}),
        }

    return {
        "statusCode": 200,
        "body": json.dumps({"claims": payload}),
    }
