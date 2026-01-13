from __future__ import annotations

import json
import os
from typing import Any

import boto3

from raja import create_token


def _parse_body(event: dict[str, Any]) -> dict[str, Any]:
    body = event.get("body")
    if isinstance(body, str):
        return json.loads(body)
    if isinstance(body, dict):
        return body
    return {}


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    principal_table_name = os.environ.get("PRINCIPAL_TABLE", "")
    jwt_secret_arn = os.environ.get("JWT_SECRET_ARN", "")
    token_ttl = int(os.environ.get("TOKEN_TTL", "3600"))

    if not principal_table_name or not jwt_secret_arn:
        return {"statusCode": 500, "body": "Missing configuration"}

    body = _parse_body(event)
    principal = body.get("principal")
    if not principal:
        return {"statusCode": 400, "body": "Missing principal"}

    dynamodb = boto3.resource("dynamodb")
    table = dynamodb.Table(principal_table_name)
    response = table.get_item(Key={"principal": principal})
    item = response.get("Item")
    if not item:
        return {"statusCode": 404, "body": "Principal not found"}

    scopes = item.get("scopes", [])

    secrets = boto3.client("secretsmanager")
    secret_response = secrets.get_secret_value(SecretId=jwt_secret_arn)
    secret = secret_response.get("SecretString", "")

    token = create_token(principal, scopes, token_ttl, secret)

    return {
        "statusCode": 200,
        "body": json.dumps({"token": token}),
    }
