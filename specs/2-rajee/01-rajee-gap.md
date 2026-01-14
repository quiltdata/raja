<!-- markdownlint-disable MD024 -->
# RAJEE Gap Analysis: Minimal Changes for Walking Skeleton

## Executive Summary

This document analyzes the **minimal changes** required to transform the current RAJA control plane into **RAJEE** (RAJA Execution Environment) - the walking skeleton specified in [GitHub issue #5](https://github.com/user/raja/issues/5).

**Current State:** Production-ready control plane for policy compilation and token issuance
**Target State:** End-to-end authorization demonstration with Lambda REQUEST authorizer protecting S3 operations

**Key Finding:** The current implementation has ~80% of issue #5 requirements. The **primary gap** is the absence of a Lambda REQUEST authorizer for API Gateway.

## Issue #5 Requirements Recap

From [GitHub issue #5](https://github.com/user/raja/issues/5):

### Goal

Prove that RAJ-based authorization can be enforced mechanically (Request ⊆ Authority) in a real AWS data plane.

### Core Requirements

1. **Protected Resource:** S3 bucket
2. **Data Plane:** API Gateway + Lambda REQUEST authorizer + Lambda handler
3. **Control Plane:** RAJA server (mint tokens, serve JWKS)
4. **Acceptance Criteria:**
   - RAJ minted by RAJA allows exactly the intended S3 operation
   - Out-of-scope requests denied solely by subset logic
   - No policy or RAJA calls on the hot path
   - Subset semantics are identical between minting and enforcement

## Current Implementation Analysis

### ✅ What Exists Today

| Component | Status | Location | Quality |
|-----------|--------|----------|---------|
| S3 protection model | ✅ Complete | [src/raja/server/routers/harness.py](../../src/raja/server/routers/harness.py) | High |
| Token minting | ✅ Complete | `POST /s3-harness/mint` (line 160-198) | High |
| JWT verification | ✅ Complete | Signature, issuer, audience, exp checks | High |
| Subset checking | ⚠️ Partial | [src/raja/scope.py:83-87](../../src/raja/scope.py#L83-L87) | Needs wildcards |
| JWKS endpoint | ⚠️ Non-standard | `GET /s3-harness/config` | Works but wrong path |
| CDK infrastructure | ✅ Complete | [infra/raja_poc/](../../infra/raja_poc/) | High |
| Core library | ✅ Complete | [src/raja/](../../src/raja/) | High |

### ❌ Critical Missing Component

**Lambda REQUEST Authorizer for API Gateway**

This is the **ONLY major missing piece** from issue #5. Everything else is implemented or requires minor adjustments.

## Minimal Changes Required

### Change 1: Add Lambda REQUEST Authorizer

**Impact:** HIGH - Core requirement from issue #5
**Effort:** 1-2 days
**Risk:** Medium (new component, needs testing)

#### What to Build

A Lambda function that:

1. Receives API Gateway Lambda REQUEST authorizer event
2. Extracts JWT from `Authorization: Bearer <token>` header
3. Verifies JWT using shared signing secret
4. Extracts scopes from JWT claims
5. Performs Request ⊆ Authority subset check
6. Returns IAM policy document (Allow/Deny)

#### File to Create

**`lambda_handlers/authorizer/handler.py`**

```python
"""Lambda REQUEST authorizer for RAJEE data plane."""

from __future__ import annotations

import json
import os
from typing import Any

import jwt
import structlog

from raja.scope import Scope, is_subset
from raja.exceptions import TokenValidationError

logger = structlog.get_logger(__name__)

def _get_signing_secret() -> str:
    """Get JWT signing secret from environment."""
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET environment variable not set")
    return secret

def _extract_token(event: dict[str, Any]) -> str:
    """Extract bearer token from authorization header."""
    auth_header = event.get("headers", {}).get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise TokenValidationError("Missing or invalid Authorization header")
    return auth_header[7:]  # Strip "Bearer "

def _extract_request_scope(event: dict[str, Any]) -> Scope:
    """Extract requested scope from API Gateway event.

    Assumes path format: /s3/{bucket}/{key+} with method mapping to action.
    """
    method = event.get("httpMethod", "")
    path_params = event.get("pathParameters", {})

    bucket = path_params.get("bucket", "")
    key = path_params.get("key", "")

    # Map HTTP method to S3 action
    action_map = {
        "GET": "s3:GetObject",
        "PUT": "s3:PutObject",
        "DELETE": "s3:DeleteObject",
        "HEAD": "s3:HeadObject",
    }

    action = action_map.get(method, "")
    if not action:
        raise ValueError(f"Unsupported HTTP method: {method}")

    return Scope(
        resource_type="S3",
        resource_id=f"{bucket}/{key}",
        action=action,
    )

def _generate_policy(principal: str, effect: str, resource: str) -> dict[str, Any]:
    """Generate IAM policy document for API Gateway."""
    return {
        "principalId": principal,
        "policyDocument": {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Action": "execute-api:Invoke",
                    "Effect": effect,
                    "Resource": resource,
                }
            ],
        },
    }

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Lambda REQUEST authorizer handler.

    Verifies JWT and checks Request ⊆ Authority using subset logic.
    Returns IAM policy allowing or denying the request.
    """
    method_arn = event.get("methodArn", "")

    logger.info(
        "authorizer_invoked",
        method=event.get("httpMethod"),
        path=event.get("path"),
        method_arn=method_arn,
    )

    try:
        # Extract and verify JWT
        token = _extract_token(event)
        secret = _get_signing_secret()

        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"verify_exp": True},
        )

        principal = payload.get("sub", "unknown")
        granted_scopes = payload.get("scopes", [])

        logger.info(
            "token_verified",
            principal=principal,
            scopes_count=len(granted_scopes),
        )

        # Extract requested scope from API Gateway event
        requested = _extract_request_scope(event)

        logger.info(
            "checking_authorization",
            principal=principal,
            requested_scope=f"{requested.resource_type}:{requested.resource_id}:{requested.action}",
        )

        # Perform subset check: Request ⊆ Authority
        if is_subset(requested, granted_scopes):
            logger.info(
                "authorization_allowed",
                principal=principal,
                requested_scope=f"{requested.resource_type}:{requested.resource_id}:{requested.action}",
            )
            return _generate_policy(principal, "Allow", method_arn)

        logger.warning(
            "authorization_denied",
            principal=principal,
            requested_scope=f"{requested.resource_type}:{requested.resource_id}:{requested.action}",
            granted_scopes=granted_scopes,
        )
        return _generate_policy(principal, "Deny", method_arn)

    except TokenValidationError as exc:
        logger.warning("token_validation_failed", error=str(exc))
        return _generate_policy("unknown", "Deny", method_arn)

    except jwt.ExpiredSignatureError:
        logger.warning("token_expired")
        return _generate_policy("unknown", "Deny", method_arn)

    except jwt.InvalidTokenError as exc:
        logger.warning("invalid_token", error=str(exc))
        return _generate_policy("unknown", "Deny", method_arn)

    except Exception as exc:
        logger.error("authorizer_error", error=str(exc), exc_info=True)
        return _generate_policy("unknown", "Deny", method_arn)
```

#### CDK Construct to Create

**`infra/raja_poc/constructs/data_plane.py`**

```python
"""Data plane stack with Lambda authorizer and protected S3 operations."""

from __future__ import annotations

import os
from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_apigateway as apigateway
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from constructs import Construct


class RajeeDataPlane(Construct):
    """RAJEE data plane with Lambda authorizer protecting S3 operations."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        jwt_signing_secret_arn: str,
        protected_bucket: s3.IBucket,
    ) -> None:
        super().__init__(scope, construct_id)

        # Lambda authorizer
        authorizer_fn = lambda_.Function(
            self,
            "Authorizer",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(Path(__file__).parent.parent.parent.parent / "lambda_handlers" / "authorizer")
            ),
            timeout=Duration.seconds(5),
            environment={
                "JWT_SECRET_ARN": jwt_signing_secret_arn,
            },
        )

        # Grant authorizer permission to read JWT secret
        authorizer_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["secretsmanager:GetSecretValue"],
                resources=[jwt_signing_secret_arn],
            )
        )

        # Data plane Lambda (performs S3 operations)
        data_plane_fn = lambda_.Function(
            self,
            "DataPlane",
            runtime=lambda_.Runtime.PYTHON_3_12,
            handler="handler.handler",
            code=lambda_.Code.from_asset(
                str(Path(__file__).parent.parent.parent.parent / "lambda_handlers" / "data_plane")
            ),
            timeout=Duration.seconds(30),
            environment={
                "PROTECTED_BUCKET": protected_bucket.bucket_name,
            },
        )

        # Grant data plane read/write access to protected bucket
        protected_bucket.grant_read_write(data_plane_fn)

        # API Gateway with Lambda authorizer
        api = apigateway.RestApi(
            self,
            "DataPlaneApi",
            rest_api_name="RAJEE Data Plane",
            description="Protected S3 operations with Lambda authorizer",
            deploy_options={"stage_name": "v1"},
        )

        # Create Lambda REQUEST authorizer
        authorizer = apigateway.RequestAuthorizer(
            self,
            "RequestAuthorizer",
            handler=authorizer_fn,
            identity_sources=[apigateway.IdentitySource.header("Authorization")],
            results_cache_ttl=Duration.minutes(5),
        )

        # S3 operations endpoint: /s3/{bucket}/{key+}
        s3_resource = api.root.add_resource("s3")
        bucket_resource = s3_resource.add_resource("{bucket}")
        key_resource = bucket_resource.add_resource("{key+}")

        # Add methods with authorizer
        for method in ["GET", "PUT", "DELETE", "HEAD"]:
            key_resource.add_method(
                method,
                apigateway.LambdaIntegration(data_plane_fn),
                authorizer=authorizer,
                authorization_type=apigateway.AuthorizationType.CUSTOM,
            )

        self.api = api
        self.authorizer = authorizer_fn
        self.data_plane = data_plane_fn
```

**Estimated LOC:** ~200 lines total (handler + CDK)

---

### Change 2: Fix Subset Checking to Support Wildcards

**Impact:** HIGH - Core requirement for "Request ⊆ Authority"
**Effort:** 4-6 hours
**Risk:** Low (pure logic, well-tested)

#### Current Issue

[src/raja/scope.py:83-87](../../src/raja/scope.py#L83-L87):

```python
def is_subset(requested: Scope, granted: Sequence[Scope | str]) -> bool:
    """Check whether a requested scope is present in the granted scopes."""
    requested_key = format_scope(requested.resource_type, requested.resource_id, requested.action)
    granted_keys = _normalize_scopes(granted)
    return requested_key in granted_keys  # ❌ EXACT MATCH ONLY
```

This performs **exact string matching** and doesn't handle wildcards.

**Examples that FAIL today:**

- `S3:my-bucket/doc.txt:GetObject` vs `S3:my-bucket/*:GetObject` → FALSE (should be TRUE)
- `S3:my-bucket/doc.txt:GetObject` vs `S3:*:*` → FALSE (should be TRUE)

#### Required Change

Update `is_subset()` to check if any granted scope pattern matches the requested scope:

```python
def _scope_matches(requested: Scope, granted_pattern: Scope) -> bool:
    """Check if a requested scope matches a granted scope pattern.

    Wildcards (*) in granted pattern match any value in that component.

    Examples:
        _scope_matches(
            Scope("S3", "bucket/key", "GetObject"),
            Scope("S3", "*", "GetObject")
        ) → True

        _scope_matches(
            Scope("S3", "bucket/key", "GetObject"),
            Scope("S3", "bucket/*", "*")
        ) → True
    """
    # Check resource_type
    if granted_pattern.resource_type != "*" and granted_pattern.resource_type != requested.resource_type:
        return False

    # Check resource_id (supports prefix matching with *)
    if granted_pattern.resource_id != "*":
        if granted_pattern.resource_id.endswith("*"):
            # Prefix match: "bucket/*" matches "bucket/anything"
            prefix = granted_pattern.resource_id[:-1]
            if not requested.resource_id.startswith(prefix):
                return False
        elif granted_pattern.resource_id != requested.resource_id:
            return False

    # Check action
    if granted_pattern.action != "*" and granted_pattern.action != requested.action:
        return False

    return True


def is_subset(requested: Scope, granted: Sequence[Scope | str]) -> bool:
    """Check whether a requested scope is covered by any granted scope.

    Returns True if ANY granted scope pattern matches the requested scope.
    """
    requested_scope = requested if isinstance(requested, Scope) else parse_scope(requested)

    for granted in granted:
        granted_scope = granted if isinstance(granted, Scope) else parse_scope(granted)
        if _scope_matches(requested_scope, granted_scope):
            logger.debug(
                "scope_match_found",
                requested=format_scope(requested_scope.resource_type, requested_scope.resource_id, requested_scope.action),
                granted=format_scope(granted_scope.resource_type, granted_scope.resource_id, granted_scope.action),
            )
            return True

    return False
```

**Testing Strategy:**

- Add comprehensive unit tests for wildcard patterns
- Test edge cases: `*:*:*`, prefix matching, exact matching
- Ensure backwards compatibility with existing non-wildcard scopes

**Estimated LOC:** ~50 lines (function + tests)

---

### Change 3: Add Standard JWKS Endpoint

**Impact:** MEDIUM - Interoperability requirement
**Effort:** 1 hour
**Risk:** Low (simple endpoint addition)

#### Current Issue

JWKS is available at `GET /s3-harness/config` (non-standard location).
Issue #5 explicitly requests: `GET /.well-known/jwks.json`

#### Required Change

Add endpoint to control plane router:

**File:** [src/raja/server/routers/control_plane.py](../../src/raja/server/routers/control_plane.py)

```python
@router.get("/.well-known/jwks.json")
def jwks_endpoint(
    secret: str = Depends(dependencies.get_jwt_secret),
) -> dict[str, Any]:
    """Standard OIDC JWKS endpoint.

    Returns public keys for JWT verification.
    """
    logger.debug("jwks_requested")

    return {
        "keys": [
            {
                "kty": "oct",
                "kid": _secret_kid(secret),
                "use": "sig",
                "alg": "HS256",
                "k": _b64url(secret.encode("utf-8")),
            }
        ]
    }
```

**Note:** This reuses the existing JWKS generation logic from [src/raja/server/routers/harness.py:112-123](../../src/raja/server/routers/harness.py#L112-L123).

**Estimated LOC:** ~15 lines

---

### Change 4: Create Data Plane Lambda Handler

**Impact:** MEDIUM - Demonstrates protected S3 operations
**Effort:** 4-6 hours
**Risk:** Low (straightforward S3 operations)

#### What to Build

A Lambda function that:

1. Receives authorized requests from API Gateway (authorizer already checked)
2. Extracts bucket/key from path parameters
3. Performs S3 operation based on HTTP method
4. Returns result to client

#### File to Create

**`lambda_handlers/data_plane/handler.py`**

```python
"""Data plane Lambda handler for protected S3 operations."""

from __future__ import annotations

import json
import os
from typing import Any

import boto3
import structlog

logger = structlog.get_logger(__name__)

s3_client = boto3.client("s3")

def _get_protected_bucket() -> str:
    """Get protected bucket name from environment."""
    bucket = os.environ.get("PROTECTED_BUCKET")
    if not bucket:
        raise RuntimeError("PROTECTED_BUCKET environment variable not set")
    return bucket

def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Data plane handler for S3 operations.

    Authorization has already been performed by Lambda authorizer.
    This handler just executes the requested S3 operation.
    """
    method = event.get("httpMethod", "")
    path_params = event.get("pathParameters", {})
    principal = event.get("requestContext", {}).get("authorizer", {}).get("principalId", "unknown")

    bucket = _get_protected_bucket()
    key = path_params.get("key", "")

    logger.info(
        "data_plane_request",
        principal=principal,
        method=method,
        bucket=bucket,
        key=key,
    )

    try:
        if method == "GET":
            # GetObject
            response = s3_client.get_object(Bucket=bucket, Key=key)
            body = response["Body"].read().decode("utf-8")

            logger.info("s3_get_success", principal=principal, bucket=bucket, key=key)

            return {
                "statusCode": 200,
                "headers": {"Content-Type": response["ContentType"]},
                "body": body,
            }

        elif method == "PUT":
            # PutObject
            body = event.get("body", "")
            s3_client.put_object(Bucket=bucket, Key=key, Body=body)

            logger.info("s3_put_success", principal=principal, bucket=bucket, key=key)

            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Object uploaded successfully"}),
            }

        elif method == "DELETE":
            # DeleteObject
            s3_client.delete_object(Bucket=bucket, Key=key)

            logger.info("s3_delete_success", principal=principal, bucket=bucket, key=key)

            return {
                "statusCode": 200,
                "body": json.dumps({"message": "Object deleted successfully"}),
            }

        elif method == "HEAD":
            # HeadObject
            response = s3_client.head_object(Bucket=bucket, Key=key)

            logger.info("s3_head_success", principal=principal, bucket=bucket, key=key)

            return {
                "statusCode": 200,
                "headers": {
                    "Content-Type": response["ContentType"],
                    "Content-Length": str(response["ContentLength"]),
                    "Last-Modified": response["LastModified"].isoformat(),
                },
                "body": "",
            }

        else:
            logger.warning("unsupported_method", method=method)
            return {
                "statusCode": 405,
                "body": json.dumps({"error": f"Method {method} not supported"}),
            }

    except s3_client.exceptions.NoSuchKey:
        logger.warning("s3_key_not_found", bucket=bucket, key=key)
        return {
            "statusCode": 404,
            "body": json.dumps({"error": "Object not found"}),
        }

    except Exception as exc:
        logger.error("s3_operation_failed", error=str(exc), exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": "Internal server error"}),
        }
```

**Estimated LOC:** ~120 lines

---

### Change 5: Update CDK Infrastructure

**Impact:** MEDIUM - Wire everything together
**Effort:** 2-3 hours
**Risk:** Low (standard CDK patterns)

#### Required Updates

**File:** [infra/raja_poc/app.py](../../infra/raja_poc/app.py)

Add data plane stack:

```python
#!/usr/bin/env python3
"""RAJEE CDK application."""

import os

import aws_cdk as cdk
from raja_poc.stacks.avp_stack import RajaAvpStack
from raja_poc.stacks.services_stack import RajaServicesStack
from raja_poc.stacks.data_plane_stack import RajeeDataPlaneStack

app = cdk.App()

# Environment
env = cdk.Environment(
    account=os.environ.get("CDK_DEFAULT_ACCOUNT"),
    region=os.environ.get("CDK_DEFAULT_REGION", "us-east-1"),
)

# AVP stack (Cedar policy store)
avp_stack = RajaAvpStack(app, "RajaAvpStack", env=env)

# Control plane stack (token minting, policy compilation)
services_stack = RajaServicesStack(
    app,
    "RajaServicesStack",
    policy_store=avp_stack.policy_store,
    env=env,
)

# Data plane stack (Lambda authorizer + protected S3)
data_plane_stack = RajeeDataPlaneStack(
    app,
    "RajeeDataPlaneStack",
    jwt_signing_secret_arn=services_stack.jwt_signing_secret.secret_arn,
    env=env,
)

app.synth()
```

**New File:** [infra/raja_poc/stacks/data_plane_stack.py](../../infra/raja_poc/stacks/data_plane_stack.py)

```python
"""RAJEE data plane stack."""

from __future__ import annotations

from aws_cdk import Stack
from aws_cdk import aws_s3 as s3
from constructs import Construct

from raja_poc.constructs.data_plane import RajeeDataPlane


class RajeeDataPlaneStack(Stack):
    """RAJEE data plane with Lambda authorizer and protected S3 bucket."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        jwt_signing_secret_arn: str,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Protected S3 bucket
        protected_bucket = s3.Bucket(
            self,
            "ProtectedBucket",
            bucket_name=f"rajee-protected-{self.account}-{self.region}",
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            enforce_ssl=True,
        )

        # Data plane with Lambda authorizer
        data_plane = RajeeDataPlane(
            self,
            "DataPlane",
            jwt_signing_secret_arn=jwt_signing_secret_arn,
            protected_bucket=protected_bucket,
        )

        self.protected_bucket = protected_bucket
        self.data_plane_api = data_plane.api
```

**Estimated LOC:** ~100 lines total

---

## Summary of Changes

| Change | Files to Create/Modify | LOC | Effort | Risk |
|--------|------------------------|-----|--------|------|
| 1. Lambda authorizer | `lambda_handlers/authorizer/handler.py` | ~120 | 1-2 days | Medium |
| 2. Wildcard subset checking | [src/raja/scope.py](../../src/raja/scope.py) | ~50 | 4-6 hrs | Low |
| 3. Standard JWKS endpoint | [src/raja/server/routers/control_plane.py](../../src/raja/server/routers/control_plane.py) | ~15 | 1 hr | Low |
| 4. Data plane handler | `lambda_handlers/data_plane/handler.py` | ~120 | 4-6 hrs | Low |
| 5. CDK updates | [infra/raja_poc/](../../infra/raja_poc/) | ~100 | 2-3 hrs | Low |

**Total Estimated Effort:** 2-3 days
**Total New/Modified LOC:** ~405 lines

## Architecture After Changes

```
┌─────────────────────────────────────────────────────────────┐
│                    RAJEE Walking Skeleton                    │
└─────────────────────────────────────────────────────────────┘

┌──────────────────────┐
│  Control Plane       │  (Existing - RajaServicesStack)
│  ┌────────────────┐  │
│  │ API Gateway    │  │
│  │   /token       │  │  ← Mint RAJ tokens
│  │   /principals  │  │  ← Manage principals
│  │   /.well-known/│  │  ← NEW: Standard JWKS
│  │    jwks.json   │  │
│  └────────┬───────┘  │
│           ▼           │
│  ┌────────────────┐  │
│  │ Control Plane  │  │
│  │ Lambda (FastAPI)│ │
│  └────────────────┘  │
└──────────────────────┘
         │
         │ (client gets JWT)
         │
         ▼
┌──────────────────────────────────────────────────────────────┐
│  Data Plane (NEW - RajeeDataPlaneStack)                      │
│                                                               │
│  ┌────────────────────────────────────────────────────────┐  │
│  │ API Gateway                                            │  │
│  │   /s3/{bucket}/{key+}                                  │  │
│  │                                                        │  │
│  │   ┌───────────────────────────────────────────┐       │  │
│  │   │ Lambda REQUEST Authorizer (NEW)           │       │  │
│  │   │ 1. Extract JWT from Authorization header  │       │  │
│  │   │ 2. Verify signature using JWKS            │       │  │
│  │   │ 3. Check Request ⊆ Authority (UPDATED)    │       │  │
│  │   │ 4. Return IAM policy (Allow/Deny)         │       │  │
│  │   └──────────────┬────────────────────────────┘       │  │
│  │                  │                                     │  │
│  │                  ▼ (if authorized)                     │  │
│  │   ┌───────────────────────────────────────────┐       │  │
│  │   │ Data Plane Lambda (NEW)                   │       │  │
│  │   │ - Perform scoped S3 operations            │       │  │
│  │   └──────────────┬────────────────────────────┘       │  │
│  └──────────────────┼────────────────────────────────────┘  │
│                     │                                        │
│                     ▼                                        │
│  ┌───────────────────────────────────────────────────────┐  │
│  │ Protected S3 Bucket (NEW)                             │  │
│  │ - All access goes through authorized API Gateway      │  │
│  └───────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────┘
```

## End-to-End Flow

### 1. Mint Token (Control Plane)

```bash
# Request
curl -X POST https://control-plane.execute-api.us-east-1.amazonaws.com/prod/token \
  -H "Content-Type: application/json" \
  -d '{
    "principal": "User::alice",
    "scopes": ["S3:my-bucket/*:GetObject", "S3:my-bucket/uploads/*:PutObject"]
  }'

# Response
{
  "token": "eyJhbGc...",
  "expires_at": 1234567890
}
```

### 2. Access Protected Resource (Data Plane)

```bash
# Request (with JWT in Authorization header)
curl https://data-plane.execute-api.us-east-1.amazonaws.com/v1/s3/my-bucket/document.txt \
  -H "Authorization: Bearer eyJhbGc..."

# Flow:
# 1. API Gateway → Lambda Authorizer
#    - Authorizer extracts JWT
#    - Verifies signature
#    - Checks: S3:my-bucket/document.txt:GetObject ⊆ [S3:my-bucket/*:GetObject, ...]
#    - Result: ALLOW (matches S3:my-bucket/*:GetObject)
#    - Returns IAM policy: Allow execute-api:Invoke
#
# 2. API Gateway → Data Plane Lambda (request authorized)
#    - Lambda performs: s3.get_object(Bucket="my-bucket", Key="document.txt")
#    - Returns object content
#
# 3. Response to client
```

### 3. Denied Request (Out of Scope)

```bash
# Request (same JWT, trying to write)
curl -X PUT https://data-plane.execute-api.us-east-1.amazonaws.com/v1/s3/my-bucket/document.txt \
  -H "Authorization: Bearer eyJhbGc..." \
  -d "new content"

# Flow:
# 1. API Gateway → Lambda Authorizer
#    - Checks: S3:my-bucket/document.txt:PutObject ⊆ [S3:my-bucket/*:GetObject, S3:my-bucket/uploads/*:PutObject]
#    - Result: DENY (document.txt not under uploads/ prefix)
#    - Returns IAM policy: Deny execute-api:Invoke
#
# 2. API Gateway responds: 403 Forbidden (Data Plane Lambda never invoked)
```

## Validation Criteria

From issue #5:

| Criterion | How to Validate | Expected Result |
|-----------|----------------|-----------------|
| RAJ minted by RAJA allows exactly the intended S3 operation | Mint token with `S3:bucket/key:GetObject`, call GET endpoint | ✅ 200 OK, object returned |
| Out-of-scope request denied solely by subset logic | Same token, call PUT endpoint (different action) | ✅ 403 Forbidden from authorizer |
| No policy or RAJA calls on hot path | Check Lambda authorizer logs | ✅ Only JWT verification + subset check, no AVP/DynamoDB calls |
| Subset semantics identical between minting and enforcement | Mint with wildcard `S3:bucket/*:GetObject`, test multiple keys | ✅ All keys under prefix allowed |

## Testing Strategy

### Unit Tests

**New tests to add:**

1. **Authorizer Handler** (`tests/unit/lambda_handlers/test_authorizer.py`)
   - Test JWT extraction from Authorization header
   - Test request scope extraction from API Gateway event
   - Test IAM policy generation (Allow/Deny)
   - Test token validation errors (expired, invalid)

2. **Wildcard Subset Checking** (`tests/unit/test_scope.py`)

   ```python
   def test_wildcard_resource_type():
       assert is_subset(
           Scope("S3", "bucket/key", "GetObject"),
           [Scope("*", "*", "*")]
       )

   def test_wildcard_resource_id_prefix():
       assert is_subset(
           Scope("S3", "bucket/uploads/file.txt", "PutObject"),
           [Scope("S3", "bucket/uploads/*", "PutObject")]
       )

   def test_wildcard_action():
       assert is_subset(
           Scope("S3", "bucket/key", "GetObject"),
           [Scope("S3", "bucket/key", "*")]
       )
   ```

3. **Data Plane Handler** (`tests/unit/lambda_handlers/test_data_plane.py`)
   - Test S3 GetObject operation
   - Test S3 PutObject operation
   - Test error handling (NoSuchKey, etc.)

### Integration Tests

**New tests to add:**

1. **End-to-End Authorization** (`tests/integration/test_rajee_e2e.py`)

   ```python
   def test_authorized_get_object():
       # 1. Mint token with GetObject scope
       token_response = requests.post(
           f"{control_plane_url}/token",
           json={
               "principal": "User::alice",
               "scopes": ["S3:my-bucket/*:GetObject"]
           }
       )
       token = token_response.json()["token"]

       # 2. Upload test object to S3
       s3_client.put_object(
           Bucket="my-bucket",
           Key="test.txt",
           Body="test content"
       )

       # 3. Access via data plane with JWT
       response = requests.get(
           f"{data_plane_url}/s3/my-bucket/test.txt",
           headers={"Authorization": f"Bearer {token}"}
       )

       assert response.status_code == 200
       assert response.text == "test content"

   def test_denied_put_object():
       # Same token (only has GetObject), try PutObject
       response = requests.put(
           f"{data_plane_url}/s3/my-bucket/test.txt",
           headers={"Authorization": f"Bearer {token}"},
           data="new content"
       )

       assert response.status_code == 403  # Denied by authorizer
   ```

2. **Authorizer Performance** (`tests/integration/test_authorizer_performance.py`)
   - Verify no AVP calls on hot path
   - Verify no DynamoDB calls on hot path
   - Measure authorizer latency (should be <50ms)

## Risk Mitigation

### Risk 1: Authorizer Performance

**Risk:** Lambda authorizer adds latency to every request
**Mitigation:**

- Use API Gateway authorizer result caching (5 minutes)
- Keep authorizer code minimal (no external calls)
- Use Lambda SnapStart for instant cold starts

### Risk 2: Wildcard Subset Logic Bugs

**Risk:** Incorrect wildcard matching could allow unauthorized access
**Mitigation:**

- Comprehensive unit tests with edge cases
- Property-based tests using hypothesis
- Fail-closed: deny if scope parsing fails
- Code review focused on security

### Risk 3: JWT Secret Exposure

**Risk:** Signing secret leaked could allow token forgery
**Mitigation:**

- Use Secrets Manager for key storage
- Never log secret values
- Rotate keys periodically
- Use short token expiration (5 minutes default)

## Implementation Plan

### Phase 1: Core Logic (1 day)

1. Update `is_subset()` with wildcard support
2. Add comprehensive unit tests
3. Add `/.well-known/jwks.json` endpoint

### Phase 2: Lambda Authorizer (1 day)

1. Create authorizer handler
2. Add unit tests for authorizer
3. Test locally with sample events

### Phase 3: Data Plane (0.5 days)

1. Create data plane Lambda handler
2. Add unit tests
3. Test locally with mock S3

### Phase 4: CDK Infrastructure (0.5 days)

1. Create data plane stack
2. Create data plane construct
3. Wire authorizer to API Gateway

### Phase 5: Integration Testing (0.5 days)

1. Deploy full stack
2. Run end-to-end tests
3. Validate acceptance criteria from issue #5

**Total: 3-4 days**

## Success Metrics

| Metric | Target | Validation |
|--------|--------|------------|
| Authorizer latency | < 50ms p99 | CloudWatch metrics |
| Subset check correctness | 100% | Unit test coverage |
| Zero AVP calls on hot path | 0 | Lambda logs |
| Zero DynamoDB calls on hot path | 0 | Lambda logs |
| Token verification success rate | > 99% | CloudWatch metrics |

## Conclusion

The current RAJA implementation is **~80% complete** relative to issue #5 requirements. The **primary gap** is the Lambda REQUEST authorizer, which requires ~405 new lines of code across 5 files.

**Key Insight:** Most of the hard work is done. The core library ([src/raja/](../../src/raja/)), infrastructure ([infra/raja_poc/](../../infra/raja_poc/)), and S3 harness ([src/raja/server/routers/harness.py](../../src/raja/server/routers/harness.py)) are production-ready. We just need to **wire them together** with an API Gateway Lambda authorizer.

**Recommended Next Steps:**

1. Implement wildcard subset checking (highest priority, affects correctness)
2. Build Lambda authorizer (core missing component)
3. Add data plane Lambda (demonstrates end-to-end flow)
4. Update CDK infrastructure (wire everything together)
5. Run integration tests (validate issue #5 acceptance criteria)

**Estimated Timeline:** 3-4 days to complete RAJEE walking skeleton.
