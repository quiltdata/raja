"""Enforcer Lambda Handler.

Makes authorization decisions by validating JWT tokens and checking scopes.
"""

import json
import os
from typing import Any

import boto3
from raja import AuthRequest, enforce

# Environment variables
JWT_SECRET_ARN = os.environ["JWT_SECRET_ARN"]

# AWS clients
secrets_client = boto3.client("secretsmanager")

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
    """Make authorization decision.

    Request body:
        {
            "token": "eyJ...",
            "request": {
                "resource_type": "Document",
                "resource_id": "doc123",
                "action": "read"
            }
        }

    Response:
        {
            "allowed": true,
            "reason": "Scope matched",
            "matched_scope": "Document:doc123:read"
        }

    Returns:
        Response with authorization decision
    """
    try:
        # Parse request body
        body = event.get("body", "{}")
        if isinstance(body, str):
            body = json.loads(body)

        token = body.get("token")
        request_data = body.get("request")

        if not token:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing required field: token"}),
            }

        if not request_data:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": "Missing required field: request"}),
            }

        # Create AuthRequest
        try:
            auth_request = AuthRequest(
                resource_type=request_data["resource_type"],
                resource_id=request_data["resource_id"],
                action=request_data["action"],
                context=request_data.get("context"),
            )
        except (KeyError, TypeError) as e:
            return {
                "statusCode": 400,
                "body": json.dumps({"error": f"Invalid request format: {e}"}),
            }

        # Get JWT secret
        jwt_secret = get_jwt_secret()

        # Make authorization decision
        decision = enforce(token_str=token, request=auth_request, secret=jwt_secret)

        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "allowed": decision.allowed,
                    "reason": decision.reason,
                    "matched_scope": decision.matched_scope,
                }
            ),
        }

    except json.JSONDecodeError:
        return {
            "statusCode": 400,
            "body": json.dumps({"error": "Invalid JSON in request body"}),
        }
    except Exception as e:
        print(f"Error making authorization decision: {e}")
        # Fail closed: return deny on any error
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "allowed": False,
                    "reason": f"Authorization error: {str(e)}",
                    "matched_scope": None,
                }
            ),
        }
