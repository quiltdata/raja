from __future__ import annotations

import json
import os
from typing import Any

import boto3

from raja import AuthRequest, enforce


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    return {}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    jwt_secret_arn = os.environ.get("JWT_SECRET_ARN", "")
    if not jwt_secret_arn:
        return {"statusCode": 500, "body": "Missing configuration"}

    body = _parse_body(event)
    token = body.get("token")
    request_data = body.get("request", {})

    if not token or not request_data:
        return {"statusCode": 400, "body": "Missing token or request"}

    secrets = boto3.client("secretsmanager")
    secret_response = secrets.get_secret_value(SecretId=jwt_secret_arn)
    secret = secret_response.get("SecretString", "")

    request = AuthRequest(**request_data)
    decision = enforce(token, request, secret)

    return {
        "statusCode": 200,
        "body": json.dumps(decision.model_dump()),
    }
