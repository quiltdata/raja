"""Token Service Lambda Handler.

Issues JWT tokens with scopes based on principal lookups from DynamoDB.
"""

import json
import os
from typing import Any

import boto3
from raja import create_token

# Environment variables
PRINCIPAL_TABLE = os.environ["PRINCIPAL_TABLE"]
JWT_SECRET_ARN = os.environ["JWT_SECRET_ARN"]
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))

# AWS clients
dynamodb = boto3.resource("dynamodb")
secrets_client = boto3.client("secretsmanager")
principal_table = dynamodb.Table(PRINCIPAL_TABLE)

# Cache JWT secret
_jwt_secret_cache: str | None = None


def get_jwt_secret() -> str:
    """Retrieve JWT secret from Secrets Manager with caching."""
    global _jwt_secret_cache
    if _jwt_secret_cache is None:
        response = secrets_client.get_secret_value(SecretId=JWT_SECRET_ARN)
        _jwt_secret_cache = response["SecretString"]
    return _jwt_secret_cache


def lambda_handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Issue JWT token for a principal.

    Request body:
        {"principal": "alice"}

    Response:
        {"token": "eyJ..."}

    Returns:
        Response with JWT token or error
    """
    try:
        # Parse request body
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        principal = body.get("principal")
        if not principal:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing required field: principal"}),
            }

        # Look up principal scopes in DynamoDB
        response = principal_table.get_item(Key={"principal": principal})
        item = response.get("Item")

        if not item:
            return {
                "statusCode": 404,
                "body": json.dumps({"error": f"Principal not found: {principal}"}),
            }

        scopes = item.get("scopes", [])

        # Get JWT secret
        jwt_secret = get_jwt_secret()

        # Create token
        token = create_token(
            subject=principal, scopes=scopes, ttl=TOKEN_TTL, secret=jwt_secret
        )

        return {
            "statusCode": 200,
            "body": json.dumps({"token": token, "principal": principal, "scopes": scopes}),
        }

    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON in request body"}),
        }
    except Exception as e:
        print(f"Error issuing token: {e}")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
