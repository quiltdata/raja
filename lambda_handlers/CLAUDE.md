# RAJA Lambda Handlers

## Overview

The `lambda_handlers/` directory contains AWS Lambda function handlers that implement RAJA's serverless API. Each handler is a thin wrapper around the core `raja` library, focusing on AWS integration (API Gateway, DynamoDB, Secrets Manager, AVP).

## Directory Structure

```
lambda_handlers/
├── __init__.py
├── compiler/
│   ├── __init__.py
│   └── handler.py              # Policy compilation handler
├── enforcer/
│   ├── __init__.py
│   └── handler.py              # Authorization enforcement handler
├── token_service/
│   ├── __init__.py
│   └── handler.py              # Token issuance handler
└── introspect/
    ├── __init__.py
    └── handler.py              # Token introspection handler
```

## Common Patterns

All Lambda handlers follow these patterns:

### 1. Environment Variables

```python
import os

POLICY_STORE_ID = os.environ["POLICY_STORE_ID"]
MAPPINGS_TABLE = os.environ["MAPPINGS_TABLE"]
JWT_SECRET_ARN = os.environ["JWT_SECRET_ARN"]
LOG_LEVEL = os.environ.get("LOG_LEVEL", "INFO")
```

### 2. Structured Logging

```python
import logging
import json

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

def log_event(event_type: str, data: dict):
    """Log structured JSON for CloudWatch Insights"""
    logger.info(json.dumps({
        "event_type": event_type,
        "timestamp": datetime.utcnow().isoformat(),
        **data
    }))
```

### 3. Error Handling

```python
def lambda_handler(event, context):
    try:
        # Handler logic
        return {
            "statusCode": 200,
            "body": json.dumps(result)
        }
    except ValueError as e:
        logger.error(f"Validation error: {e}")
        return {
            "statusCode": 400,
            "body": json.dumps({"error": str(e)})
        }
    except Exception as e:
        logger.exception("Unexpected error")
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"})
        }
```

### 4. CORS Headers

```python
CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS"
}

def create_response(status_code: int, body: dict) -> dict:
    return {
        "statusCode": status_code,
        "headers": CORS_HEADERS,
        "body": json.dumps(body)
    }
```

## Lambda Handlers

### 1. Compiler Handler (`compiler/handler.py`)

**Purpose:** Compile Cedar policies from AVP to scope strings and store in DynamoDB

**Trigger:**
- EventBridge scheduled rule (e.g., every 5 minutes)
- Manual invocation via API or CLI
- AVP policy update events (future)

**Environment Variables:**
- `POLICY_STORE_ID` - AVP policy store identifier
- `MAPPINGS_TABLE` - DynamoDB table for policy → scopes mappings
- `PRINCIPAL_TABLE` - DynamoDB table for principal → scopes mappings

**IAM Permissions:**
- `verifiedpermissions:ListPolicies`
- `verifiedpermissions:GetPolicy`
- `dynamodb:PutItem` (both tables)

**Event Format:**
```json
{
  "source": "aws.events",
  "detail-type": "Scheduled Event"
}
```

**Handler Logic:**

```python
import boto3
from raja import compile_policy
from datetime import datetime

avp_client = boto3.client("verifiedpermissions")
dynamodb = boto3.resource("dynamodb")

def lambda_handler(event, context):
    """Compile all policies from AVP and store in DynamoDB"""

    # 1. Fetch policies from AVP
    policies = list_all_policies(POLICY_STORE_ID)
    log_event("compilation_started", {
        "policy_count": len(policies)
    })

    # 2. Compile each policy
    compiled_policies = []
    principal_scopes = {}  # principal → list of scopes

    for policy in policies:
        try:
            result = compile_policy(policy["definition"])

            # Store policy mapping
            store_policy_mapping(
                policy_id=policy["policyId"],
                principal=result.principal,
                scopes=result.scopes
            )

            # Aggregate scopes by principal
            if result.principal not in principal_scopes:
                principal_scopes[result.principal] = []
            principal_scopes[result.principal].extend(result.scopes)

            compiled_policies.append(result)

        except Exception as e:
            logger.error(f"Failed to compile policy {policy['policyId']}: {e}")
            continue

    # 3. Store principal mappings
    for principal, scopes in principal_scopes.items():
        store_principal_mapping(
            principal=principal,
            scopes=list(set(scopes)),  # Deduplicate
            timestamp=datetime.utcnow().isoformat()
        )

    log_event("compilation_completed", {
        "compiled_count": len(compiled_policies),
        "principal_count": len(principal_scopes)
    })

    return {
        "statusCode": 200,
        "body": json.dumps({
            "compiled_count": len(compiled_policies),
            "principals": list(principal_scopes.keys())
        })
    }

def list_all_policies(policy_store_id: str) -> list:
    """List all policies from AVP (paginated)"""
    policies = []
    paginator = avp_client.get_paginator("list_policies")
    for page in paginator.paginate(policyStoreId=policy_store_id):
        policies.extend(page["policies"])
    return policies

def store_policy_mapping(policy_id: str, principal: str, scopes: list[str]):
    """Store policy → scopes mapping in DynamoDB"""
    table = dynamodb.Table(MAPPINGS_TABLE)
    table.put_item(Item={
        "policy_id": policy_id,
        "principal": principal,
        "scopes": scopes,
        "timestamp": datetime.utcnow().isoformat()
    })

def store_principal_mapping(principal: str, scopes: list[str], timestamp: str):
    """Store principal → scopes mapping in DynamoDB"""
    table = dynamodb.Table(PRINCIPAL_TABLE)
    table.put_item(Item={
        "principal": principal,
        "scopes": scopes,
        "timestamp": timestamp
    })
```

**Response:**
```json
{
  "statusCode": 200,
  "body": {
    "compiled_count": 5,
    "principals": ["User::alice", "User::bob", "User::admin"]
  }
}
```

### 2. Token Service Handler (`token_service/handler.py`)

**Purpose:** Issue JWT tokens with scopes for authenticated principals

**API Endpoint:** `POST /token`

**Environment Variables:**
- `PRINCIPAL_TABLE` - DynamoDB table with principal → scopes mappings
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT signing key

**IAM Permissions:**
- `dynamodb:GetItem` (PRINCIPAL_TABLE)
- `secretsmanager:GetSecretValue` (JWT_SECRET_ARN)

**Request Body:**
```json
{
  "principal": "User::alice",
  "expiration_minutes": 60  // Optional, default 60
}
```

**Handler Logic:**

```python
import boto3
from raja import create_token

dynamodb = boto3.resource("dynamodb")
secrets_client = boto3.client("secretsmanager")

# Cache JWT secret (Lambda execution context reuse)
_jwt_secret = None

def get_jwt_secret() -> str:
    """Get JWT signing secret from Secrets Manager (cached)"""
    global _jwt_secret
    if _jwt_secret is None:
        response = secrets_client.get_secret_value(SecretId=JWT_SECRET_ARN)
        _jwt_secret = response["SecretString"]
    return _jwt_secret

def lambda_handler(event, context):
    """Issue JWT token with scopes for principal"""

    # Parse request
    body = json.loads(event["body"])
    principal = body["principal"]
    expiration_minutes = body.get("expiration_minutes", 60)

    log_event("token_requested", {"principal": principal})

    # 1. Get principal scopes from DynamoDB
    table = dynamodb.Table(PRINCIPAL_TABLE)
    response = table.get_item(Key={"principal": principal})

    if "Item" not in response:
        log_event("principal_not_found", {"principal": principal})
        return create_response(404, {
            "error": "Principal not found",
            "principal": principal
        })

    scopes = response["Item"]["scopes"]

    # 2. Create JWT token
    secret = get_jwt_secret()
    token = create_token(
        principal=principal,
        scopes=scopes,
        secret=secret,
        expiration_minutes=expiration_minutes
    )

    log_event("token_issued", {
        "principal": principal,
        "scope_count": len(scopes),
        "expiration_minutes": expiration_minutes
    })

    return create_response(200, {
        "token": token,
        "principal": principal,
        "scopes": scopes,
        "expires_in": expiration_minutes * 60
    })
```

**Response:**
```json
{
  "statusCode": 200,
  "body": {
    "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
    "principal": "User::alice",
    "scopes": ["Document:doc123:read", "Document:doc456:write"],
    "expires_in": 3600
  }
}
```

### 3. Enforcer Handler (`enforcer/handler.py`)

**Purpose:** Validate tokens and check authorization via pure subset checking

**API Endpoint:** `POST /authorize`

**Environment Variables:**
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT signing key

**IAM Permissions:**
- `secretsmanager:GetSecretValue` (JWT_SECRET_ARN)

**Request Body:**
```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "resource": "Document::doc123",
  "action": "read"
}
```

**Handler Logic:**

```python
import boto3
from raja import enforce

secrets_client = boto3.client("secretsmanager")
_jwt_secret = None

def get_jwt_secret() -> str:
    """Get JWT signing secret from Secrets Manager (cached)"""
    global _jwt_secret
    if _jwt_secret is None:
        response = secrets_client.get_secret_value(SecretId=JWT_SECRET_ARN)
        _jwt_secret = response["SecretString"]
    return _jwt_secret

def lambda_handler(event, context):
    """Check authorization via token validation and subset checking"""

    # Parse request
    body = json.loads(event["body"])
    token = body["token"]
    resource = body["resource"]
    action = body["action"]

    log_event("authorization_requested", {
        "resource": resource,
        "action": action
    })

    # Enforce authorization
    secret = get_jwt_secret()
    decision = enforce(
        token=token,
        resource=resource,
        action=action,
        secret=secret
    )

    log_event("authorization_decision", {
        "decision": decision.decision,
        "resource": resource,
        "action": action,
        "reason": decision.reason
    })

    return create_response(200, {
        "decision": decision.decision,
        "reason": decision.reason,
        "requested_scope": decision.requested_scope,
        "granted_scopes": decision.granted_scopes
    })
```

**Response:**
```json
{
  "statusCode": 200,
  "body": {
    "decision": "ALLOW",
    "reason": "Requested scope Document:doc123:read is covered by granted scope Document:*:read",
    "requested_scope": "Document:doc123:read",
    "granted_scopes": ["Document:*:read", "Document:doc456:write"]
  }
}
```

### 4. Introspect Handler (`introspect/handler.py`)

**Purpose:** Decode JWT tokens and return claims (for debugging/monitoring)

**API Endpoint:** `GET /introspect`

**Environment Variables:**
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT signing key

**IAM Permissions:**
- `secretsmanager:GetSecretValue` (JWT_SECRET_ARN)

**Request Headers:**
```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Handler Logic:**

```python
import boto3
from raja import decode_token

secrets_client = boto3.client("secretsmanager")
_jwt_secret = None

def get_jwt_secret() -> str:
    """Get JWT signing secret from Secrets Manager (cached)"""
    global _jwt_secret
    if _jwt_secret is None:
        response = secrets_client.get_secret_value(SecretId=JWT_SECRET_ARN)
        _jwt_secret = response["SecretString"]
    return _jwt_secret

def lambda_handler(event, context):
    """Decode and return token claims"""

    # Extract token from Authorization header
    auth_header = event["headers"].get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return create_response(400, {
            "error": "Missing or invalid Authorization header"
        })

    token = auth_header[7:]  # Remove "Bearer " prefix

    # Decode token
    try:
        secret = get_jwt_secret()
        decoded = decode_token(token, secret)

        log_event("token_introspected", {
            "principal": decoded.principal,
            "scope_count": len(decoded.scopes)
        })

        return create_response(200, {
            "principal": decoded.principal,
            "scopes": decoded.scopes,
            "iat": decoded.iat,
            "exp": decoded.exp,
            "active": decoded.exp > datetime.utcnow().timestamp()
        })

    except jwt.ExpiredSignatureError:
        return create_response(401, {
            "error": "Token expired",
            "active": False
        })

    except jwt.InvalidTokenError as e:
        return create_response(401, {
            "error": f"Invalid token: {str(e)}",
            "active": False
        })
```

**Response (Success):**
```json
{
  "statusCode": 200,
  "body": {
    "principal": "User::alice",
    "scopes": ["Document:doc123:read", "Document:doc456:write"],
    "iat": 1704067200,
    "exp": 1704070800,
    "active": true
  }
}
```

**Response (Expired):**
```json
{
  "statusCode": 401,
  "body": {
    "error": "Token expired",
    "active": false
  }
}
```

## Performance Optimization

### 1. Secret Caching

JWT secret is cached in Lambda execution context:

```python
_jwt_secret = None  # Module-level cache

def get_jwt_secret() -> str:
    global _jwt_secret
    if _jwt_secret is None:
        # Fetch from Secrets Manager (slow)
        response = secrets_client.get_secret_value(SecretId=JWT_SECRET_ARN)
        _jwt_secret = response["SecretString"]
    return _jwt_secret  # Return cached value (fast)
```

**Benefit:** Reduces Secrets Manager API calls by ~95%

### 2. Connection Reuse

Boto3 clients are created outside handler:

```python
# Module-level (reused across invocations)
dynamodb = boto3.resource("dynamodb")
secrets_client = boto3.client("secretsmanager")

def lambda_handler(event, context):
    # Use existing connections
    table = dynamodb.Table(PRINCIPAL_TABLE)
```

**Benefit:** Faster cold starts and better throughput

### 3. DynamoDB Single-Table Design

Both mappings in same table (future optimization):

```python
# Primary key: PK (partition) + SK (sort)
# Policy mapping: PK=POLICY#{policy_id}, SK=POLICY
# Principal mapping: PK=PRINCIPAL#{principal}, SK=PRINCIPAL

# Query by principal (single request)
response = table.query(
    KeyConditionExpression="PK = :pk",
    ExpressionAttributeValues={":pk": f"PRINCIPAL#{principal}"}
)
```

**Benefit:** Fewer DynamoDB tables to manage

## Error Handling

### Standard Error Responses

```python
# 400 Bad Request - Invalid input
{
  "statusCode": 400,
  "body": {"error": "Missing required field: principal"}
}

# 401 Unauthorized - Invalid/expired token
{
  "statusCode": 401,
  "body": {"error": "Token expired"}
}

# 404 Not Found - Resource doesn't exist
{
  "statusCode": 404,
  "body": {"error": "Principal not found"}
}

# 500 Internal Server Error - Unexpected error
{
  "statusCode": 500,
  "body": {"error": "Internal server error"}
}
```

### Logging Errors

```python
try:
    # Handler logic
except ValueError as e:
    # Expected validation error
    logger.error(f"Validation error: {e}", exc_info=False)

except Exception as e:
    # Unexpected error - log full traceback
    logger.exception("Unexpected error")
```

## Testing Lambda Handlers

### Local Testing

```bash
# Use test_local.py script
python scripts/test_local.py

# Or invoke directly with sample event
python -c "
from lambda_handlers.token_service.handler import lambda_handler
event = {'body': '{\"principal\": \"User::alice\"}'}
result = lambda_handler(event, None)
print(result)
"
```

### Integration Testing

```bash
# Deploy infrastructure first
./scripts/deploy.sh

# Run integration tests
./poe test-integration

# Test specific handler
pytest tests/integration/test_token_service.py
```

### Manual API Testing

```bash
# Get API URL
export API_URL=$(aws cloudformation describe-stacks \
  --stack-name RajaPocServicesStack \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text)

# Request token
curl -X POST $API_URL/token \
  -H "Content-Type: application/json" \
  -d '{"principal": "User::alice"}'

# Check authorization
curl -X POST $API_URL/authorize \
  -H "Content-Type: application/json" \
  -d '{
    "token": "<token-from-above>",
    "resource": "Document::doc123",
    "action": "read"
  }'

# Introspect token
curl -X GET $API_URL/introspect \
  -H "Authorization: Bearer <token>"
```

## Monitoring

### CloudWatch Metrics

- **Invocations:** Number of Lambda invocations
- **Duration:** Execution time (p50, p99, max)
- **Errors:** Number of failed invocations
- **Throttles:** Number of throttled requests

### Custom Metrics

Log structured JSON for CloudWatch Insights:

```python
log_event("authorization_decision", {
    "decision": "ALLOW",
    "resource_type": "Document",
    "action": "read",
    "duration_ms": 45
})
```

Query with CloudWatch Insights:

```
fields @timestamp, event_type, decision, resource_type, action
| filter event_type = "authorization_decision"
| stats count() by decision, resource_type
```

### Alarms

Recommended CloudWatch alarms:

- Lambda errors > 1% (5 minutes)
- Lambda duration p99 > 3 seconds (5 minutes)
- Lambda throttles > 10 (1 minute)
- API Gateway 5xx errors > 5% (5 minutes)

## Best Practices

1. **Keep handlers thin** - Business logic in `raja` library
2. **Cache secrets** - Reduce Secrets Manager API calls
3. **Structured logging** - Use JSON for CloudWatch Insights
4. **Fail fast** - Validate inputs early
5. **Idempotency** - Make handlers idempotent where possible
6. **Graceful degradation** - Handle partial failures
7. **Type hints** - Use type hints for clarity
8. **Error context** - Include relevant context in errors
