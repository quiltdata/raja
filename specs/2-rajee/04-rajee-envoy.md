# RAJEE with Envoy: True S3 Proxy Architecture

## Executive Summary

This document describes the **correct architecture** for RAJEE (RAJA Execution Environment): an Envoy-based S3 proxy that provides transparent, prefix-based authorization for native S3 API calls.

**Key Insight:** RAJEE must be a **true S3 proxy** that boto3 clients can use natively, not a custom REST API that happens to call S3.

## Problem Statement

The previous designs ([01-rajee-gap.md](01-rajee-gap.md), [03-rajee-design.md](03-rajee-design.md)) proposed using API Gateway + Lambda authorizer with custom endpoints like `/s3/{bucket}/{key+}`. This approach has fundamental limitations:

### Why API Gateway + Custom Endpoints Fails

1. **Not S3-compatible**: boto3 cannot point to custom API Gateway endpoints
2. **Incomplete S3 API coverage**: Would require implementing dozens of S3 operations manually
3. **Complex transformations**: Every S3 operation needs custom request/response mapping
4. **No streaming support**: Lambda/API Gateway have size limits
5. **Not a proxy**: It's a custom API that forwards to S3, not a transparent proxy

### What We Actually Need

```python
# This should work:
s3_client = boto3.client('s3', endpoint_url='https://rajee.example.com')
s3_client.get_object(Bucket='my-bucket', Key='my-file.txt')  # Native S3 API

# Not this:
requests.get('https://rajee.example.com/s3/my-bucket/my-file.txt')  # Custom API
```

## Design Principle

> **RAJEE is a transparent S3 proxy with authorization, not an S3 API reimplementation.**

## Architecture Overview

```
┌─────────────────────┐
│  boto3 Client       │
│  endpoint_url=      │
│  https://rajee.com  │
└──────────┬──────────┘
           │
           │ Native S3 REST API calls
           │ (GetObject, PutObject, ListBucket, etc.)
           │
           ▼
┌───────────────────────────────────────────────────────────┐
│  Envoy Proxy (RAJEE)                                      │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  External Authorization Filter                      │ │
│  │                                                     │ │
│  │  1. Extract JWT from Authorization header          │ │
│  │  2. Extract S3 operation from request              │ │
│  │     - Method + Path → s3:GetObject/bucket/key      │ │
│  │  3. Call authorizer service (gRPC/HTTP)            │ │
│  │  4. Receive ALLOW/DENY decision                    │ │
│  │                                                     │ │
│  │  If DENY: Return 403 Forbidden                     │ │
│  │  If ALLOW: Continue to upstream                    │ │
│  └─────────────────────────────────────────────────────┘ │
│                                                           │
│  ┌─────────────────────────────────────────────────────┐ │
│  │  Upstream Cluster: Real S3                         │ │
│  │  - Forward authorized requests unmodified          │ │
│  │  - Stream responses back to client                 │ │
│  └─────────────────────────────────────────────────────┘ │
└───────────────────────────────────────────────────────────┘
           │
           │ Forwarded S3 API calls
           │ (with original IAM credentials)
           ▼
┌─────────────────────┐
│  Amazon S3          │
│  (us-east-1)        │
└─────────────────────┘


┌───────────────────────────────────────────────────────────┐
│  Authorizer Service (Lambda or ECS)                       │
│                                                           │
│  1. Verify JWT signature                                 │
│  2. Extract grants from token                            │
│  3. Perform prefix check:                                │
│     any(request.startswith(grant) for grant in grants)   │
│  4. Return gRPC/HTTP response: ALLOW or DENY             │
│                                                           │
│  NO policy evaluation, NO database calls                 │
└───────────────────────────────────────────────────────────┘
```

## Component Details

### 1. Envoy Proxy

**Purpose:** Transparent S3 proxy with authorization filtering

**Configuration:** [envoy.yaml](envoy.yaml)

**Key Features:**
- **External Authorization Filter**: Calls authorizer service before forwarding requests
- **S3 Upstream Cluster**: Proxies to real S3 (e.g., `s3.us-east-1.amazonaws.com`)
- **Request/Response Streaming**: No size limits
- **Native S3 API**: Zero S3-specific logic in Envoy

**Deployment:**
- ECS Fargate service
- Application Load Balancer in front
- Custom domain: `rajee.example.com`

### 2. External Authorizer Service

**Purpose:** JWT validation and prefix-based authorization

**Implementation Options:**

#### Option A: Lambda Function (Simplest)
- Lambda function invoked via HTTP by Envoy ext_authz filter
- HTTP API (not gRPC for simplicity)
- Fast response (<10ms)

#### Option B: ECS Sidecar (Lowest Latency)
- Authorizer runs as sidecar container alongside Envoy
- gRPC communication (faster than HTTP)
- Shared network namespace

**Logic:**
```python
def authorize(request: AuthzRequest) -> AuthzResponse:
    """Authorize S3 request using prefix matching."""

    # 1. Extract JWT from Authorization header
    token = extract_bearer_token(request.headers["authorization"])

    # 2. Verify JWT signature
    payload = jwt.decode(token, secret, algorithms=["HS256"])
    grants = payload["grants"]  # ["s3:GetObject/bucket/", ...]

    # 3. Construct request string from HTTP request
    s3_request = construct_request_string(
        method=request.method,
        path=request.path,
        query=request.query
    )
    # Example: "s3:GetObject/my-bucket/path/to/file.txt"

    # 4. Prefix check
    for grant in grants:
        if s3_request.startswith(grant):
            return AuthzResponse(status=OK)

    return AuthzResponse(status=DENIED)
```

### 3. Request String Construction

**Critical component**: Map HTTP requests to grant strings for prefix matching.

#### S3 Request Format

S3 uses two URL styles:

**Path-style (what we'll use):**
```
https://rajee.example.com/bucket-name/key/path/to/object
```

**Virtual-hosted-style (optional future support):**
```
https://bucket-name.rajee.example.com/key/path/to/object
```

#### Mapping HTTP to S3 Operations

| HTTP Method | S3 Operation | Request String |
|-------------|--------------|----------------|
| GET /bucket/key | GetObject | `s3:GetObject/bucket/key` |
| PUT /bucket/key | PutObject | `s3:PutObject/bucket/key` |
| DELETE /bucket/key | DeleteObject | `s3:DeleteObject/bucket/key` |
| HEAD /bucket/key | HeadObject | `s3:HeadObject/bucket/key` |
| GET /bucket?list-type=2 | ListObjectsV2 | `s3:ListBucket/bucket/` |

**Note:** ListBucket is a bucket-level operation, so the key is empty (just `bucket/`)

#### Query Parameter Handling

S3 uses query parameters to distinguish operations:

```python
def get_s3_operation(method: str, path: str, query: dict) -> str:
    """Determine S3 operation from HTTP request."""

    # ListObjectsV2
    if method == "GET" and "list-type" in query:
        return "s3:ListBucket"

    # ListObjectsV1
    if method == "GET" and path.count("/") == 1:  # Just /bucket
        return "s3:ListBucket"

    # Object operations
    if method == "GET":
        return "s3:GetObject"
    elif method == "PUT":
        return "s3:PutObject"
    elif method == "DELETE":
        return "s3:DeleteObject"
    elif method == "HEAD":
        return "s3:HeadObject"

    return "s3:Unknown"
```

### 4. Grant Format (from 02-prefix-authorization-design.md)

**Format:**
```
{action}/{bucket}/{prefix}
```

**Examples:**
```
s3:GetObject/my-bucket/                    # Read entire bucket
s3:PutObject/my-bucket/uploads/            # Write to uploads prefix
s3:ListBucket/my-bucket/                   # List bucket contents
s3:DeleteObject/my-bucket/uploads/user123/ # Delete from user path
```

**Authorization:**
```python
request = "s3:GetObject/my-bucket/path/to/file.txt"
grants = [
    "s3:GetObject/my-bucket/",
    "s3:PutObject/my-bucket/uploads/"
]

# Check: does request start with any grant?
authorized = any(request.startswith(grant) for grant in grants)
# Result: True (matches "s3:GetObject/my-bucket/")
```

## Deployment Architecture

### AWS Infrastructure

```
┌─────────────────────────────────────────────────────────────┐
│  VPC                                                        │
│                                                             │
│  ┌───────────────────────────────────────────────────────┐ │
│  │  Public Subnets                                       │ │
│  │                                                       │ │
│  │  ┌─────────────────────────────────────────────────┐ │ │
│  │  │  Application Load Balancer                      │ │ │
│  │  │  - rajee.example.com                            │ │ │
│  │  │  - TLS termination                              │ │ │
│  │  └────────────────┬────────────────────────────────┘ │ │
│  └─────────────────────────────────────────────────────── │
│                      │                                     │
│  ┌───────────────────▼───────────────────────────────────┐ │
│  │  Private Subnets                                      │ │
│  │                                                       │ │
│  │  ┌─────────────────────────────────────────────────┐ │ │
│  │  │  ECS Fargate Service (Envoy Proxy)             │ │ │
│  │  │                                                 │ │ │
│  │  │  Container 1: Envoy Proxy                      │ │ │
│  │  │  Container 2: Authorizer (optional sidecar)    │ │ │
│  │  │                                                 │ │ │
│  │  │  Auto-scaling: 2-10 tasks                      │ │ │
│  │  └─────────────────────────────────────────────────┘ │ │
│  └───────────────────────────────────────────────────────┘ │
│                                                             │
└─────────────────────────────────────────────────────────────┘

External:
┌─────────────────────┐
│  Amazon S3          │  (us-east-1)
└─────────────────────┘

┌─────────────────────┐
│  Lambda Authorizer  │  (if not using sidecar)
└─────────────────────┘
```

### Components

1. **Application Load Balancer**
   - Public-facing endpoint: `rajee.example.com`
   - TLS termination with ACM certificate
   - Health checks to Envoy admin endpoint

2. **ECS Fargate Service**
   - Task definition with Envoy container
   - Optional: Authorizer sidecar container
   - IAM role with S3 access
   - Auto-scaling based on request rate

3. **Envoy Container**
   - Image: `envoyproxy/envoy:v1.28-latest`
   - Configuration mounted from S3 or Parameter Store
   - Admin interface on port 9901
   - Proxy port 10000

4. **Authorizer Service**
   - Option A: Lambda function (HTTP API)
   - Option B: Sidecar container (gRPC)
   - JWT secret from Secrets Manager

## Envoy Configuration

### envoy.yaml (Simplified)

```yaml
static_resources:
  listeners:
  - name: listener_0
    address:
      socket_address:
        address: 0.0.0.0
        port_value: 10000
    filter_chains:
    - filters:
      - name: envoy.filters.network.http_connection_manager
        typed_config:
          "@type": type.googleapis.com/envoy.extensions.filters.network.http_connection_manager.v3.HttpConnectionManager
          stat_prefix: ingress_http
          route_config:
            name: s3_route
            virtual_hosts:
            - name: s3_proxy
              domains: ["*"]
              routes:
              - match:
                  prefix: "/"
                route:
                  cluster: s3_upstream
                  timeout: 300s
          http_filters:
          # External authorization filter (CRITICAL)
          - name: envoy.filters.http.ext_authz
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz
              http_service:
                server_uri:
                  uri: http://localhost:9000/authorize  # Lambda or sidecar
                  cluster: authorizer
                  timeout: 0.5s
                authorization_request:
                  allowed_headers:
                    patterns:
                    - exact: authorization
                    - prefix: x-amz-
                authorization_response:
                  allowed_upstream_headers:
                    patterns:
                    - exact: x-authz-*
          # Router filter (forwards request)
          - name: envoy.filters.http.router
            typed_config:
              "@type": type.googleapis.com/envoy.extensions.filters.http.router.v3.Router

  clusters:
  # Real S3 upstream
  - name: s3_upstream
    type: LOGICAL_DNS
    dns_lookup_family: V4_ONLY
    load_assignment:
      cluster_name: s3_upstream
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: s3.us-east-1.amazonaws.com
                port_value: 443
    transport_socket:
      name: envoy.transport_sockets.tls
      typed_config:
        "@type": type.googleapis.com/envoy.extensions.transport_sockets.tls.v3.UpstreamTlsContext
        sni: s3.us-east-1.amazonaws.com

  # Authorizer service
  - name: authorizer
    type: STATIC
    load_assignment:
      cluster_name: authorizer
      endpoints:
      - lb_endpoints:
        - endpoint:
            address:
              socket_address:
                address: 127.0.0.1  # Sidecar or Lambda Function URL
                port_value: 9000

admin:
  address:
    socket_address:
      address: 0.0.0.0
      port_value: 9901
```

## Authorizer Service Implementation

### HTTP API (Lambda or Sidecar)

**Request from Envoy:**
```http
POST /authorize HTTP/1.1
Host: localhost:9000
Content-Type: application/json

{
  "attributes": {
    "request": {
      "http": {
        "method": "GET",
        "path": "/my-bucket/path/to/file.txt",
        "headers": {
          "authorization": "Bearer eyJhbGc...",
          "host": "rajee.example.com"
        }
      }
    }
  }
}
```

**Response:**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "result": {
    "allowed": true
  }
}
```

**Or denied:**
```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "result": {
    "allowed": false,
    "status": {
      "code": 7,  # PERMISSION_DENIED
      "message": "Insufficient permissions"
    }
  }
}
```

### Python Implementation

```python
"""RAJEE Authorizer Service - HTTP API for Envoy ext_authz."""

from __future__ import annotations

import os
from typing import Any

import jwt
import structlog
from fastapi import FastAPI, Request

logger = structlog.get_logger(__name__)

app = FastAPI()


def get_signing_secret() -> str:
    """Get JWT signing secret from environment."""
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET not set")
    return secret


def extract_bearer_token(auth_header: str) -> str:
    """Extract JWT from Authorization header."""
    if not auth_header.startswith("Bearer "):
        raise ValueError("Invalid Authorization header")
    return auth_header[7:]


def construct_request_string(method: str, path: str, query: dict[str, str]) -> str:
    """Construct S3 request string from HTTP request.

    Examples:
        GET /bucket/key        → s3:GetObject/bucket/key
        PUT /bucket/key        → s3:PutObject/bucket/key
        GET /bucket?list-type=2 → s3:ListBucket/bucket/
    """
    # Parse path: /bucket/key/path → ["bucket", "key", "path"]
    parts = [p for p in path.split("/") if p]

    if not parts:
        raise ValueError("Invalid path: empty")

    bucket = parts[0]
    key = "/".join(parts[1:]) if len(parts) > 1 else ""

    # Determine S3 action
    if method == "GET" and ("list-type" in query or not key):
        # ListBucket operation
        action = "s3:ListBucket"
        resource = f"{bucket}/"
    elif method == "GET":
        action = "s3:GetObject"
        resource = f"{bucket}/{key}"
    elif method == "PUT":
        action = "s3:PutObject"
        resource = f"{bucket}/{key}"
    elif method == "DELETE":
        action = "s3:DeleteObject"
        resource = f"{bucket}/{key}"
    elif method == "HEAD":
        action = "s3:HeadObject"
        resource = f"{bucket}/{key}"
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")

    return f"{action}/{resource}"


def is_authorized(request_string: str, grants: list[str]) -> bool:
    """Check if request is covered by any grant using prefix matching."""
    for grant in grants:
        if request_string.startswith(grant):
            logger.debug(
                "authorization_granted",
                request=request_string,
                grant=grant,
            )
            return True

    logger.warning(
        "authorization_denied",
        request=request_string,
        grants=grants,
    )
    return False


@app.post("/authorize")
async def authorize(request: Request) -> dict[str, Any]:
    """Envoy external authorization endpoint.

    Verifies JWT and performs prefix-based authorization check.
    """
    body = await request.json()

    # Extract request attributes from Envoy
    http_request = body.get("attributes", {}).get("request", {}).get("http", {})
    method = http_request.get("method", "")
    path = http_request.get("path", "")
    headers = http_request.get("headers", {})
    query = http_request.get("query_params", {})

    logger.info(
        "authz_request",
        method=method,
        path=path,
    )

    try:
        # 1. Extract and verify JWT
        auth_header = headers.get("authorization", "")
        token = extract_bearer_token(auth_header)

        secret = get_signing_secret()
        payload = jwt.decode(
            token,
            secret,
            algorithms=["HS256"],
            options={"verify_exp": True},
        )

        principal = payload.get("sub", "unknown")
        grants = payload.get("grants", [])

        logger.info(
            "token_verified",
            principal=principal,
            grants_count=len(grants),
        )

        # 2. Construct request string
        request_string = construct_request_string(method, path, query)

        logger.info(
            "checking_authorization",
            principal=principal,
            request=request_string,
        )

        # 3. Prefix authorization check
        if is_authorized(request_string, grants):
            return {
                "result": {
                    "allowed": True,
                }
            }

        return {
            "result": {
                "allowed": False,
                "status": {
                    "code": 7,  # PERMISSION_DENIED
                    "message": "Request not covered by any grant",
                },
            }
        }

    except jwt.ExpiredSignatureError:
        logger.warning("token_expired")
        return {
            "result": {
                "allowed": False,
                "status": {
                    "code": 16,  # UNAUTHENTICATED
                    "message": "Token expired",
                },
            }
        }

    except (jwt.InvalidTokenError, ValueError) as exc:
        logger.warning("authorization_error", error=str(exc))
        return {
            "result": {
                "allowed": False,
                "status": {
                    "code": 16,  # UNAUTHENTICATED
                    "message": "Invalid token or request",
                },
            }
        }

    except Exception as exc:
        logger.error("authorizer_error", error=str(exc), exc_info=True)
        return {
            "result": {
                "allowed": False,
                "status": {
                    "code": 13,  # INTERNAL
                    "message": "Internal authorization error",
                },
            }
        }


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy"}
```

## CDK Infrastructure

### ECS Fargate Stack

```python
"""RAJEE Data Plane Stack - Envoy S3 Proxy on ECS Fargate."""

from __future__ import annotations

from pathlib import Path

from aws_cdk import Duration, Stack
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_ecs_patterns as ecs_patterns
from aws_cdk import aws_elasticloadbalancingv2 as elbv2
from aws_cdk import RemovalPolicy
from aws_cdk import aws_iam as iam
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_secretsmanager as secretsmanager
from constructs import Construct


class RajeeEnvoyStack(Stack):
    """RAJEE S3 proxy using Envoy on ECS Fargate."""

    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        jwt_signing_secret: secretsmanager.ISecret,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # VPC
        vpc = ec2.Vpc(
            self,
            "RajeeVpc",
            max_azs=2,
            nat_gateways=1,
        )

        # ECS Cluster
        cluster = ecs.Cluster(
            self,
            "RajeeCluster",
            vpc=vpc,
            container_insights=True,
        )

        # Task Definition
        task_definition = ecs.FargateTaskDefinition(
            self,
            "RajeeTask",
            memory_limit_mib=512,
            cpu=256,
        )

        # Dedicated test bucket for proxy validation
        test_bucket = s3.Bucket(
            self,
            "RajeeTestBucket",
            bucket_name=f"raja-poc-test-{Stack.of(self).account}-{Stack.of(self).region}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            encryption=s3.BucketEncryption.S3_MANAGED,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            versioned=True,
        )

        # Grant S3 access (for proxying)
        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                resources=[test_bucket.bucket_arn, f"{test_bucket.bucket_arn}/*"],
            )
        )

        task_definition.add_to_task_role_policy(
            iam.PolicyStatement(
                actions=["s3:ListBucket"],
                resources=[test_bucket.bucket_arn],
            )
        )

        # Grant access to JWT secret
        jwt_signing_secret.grant_read(task_definition.task_role)

        # Envoy Container
        envoy_container = task_definition.add_container(
            "EnvoyProxy",
            image=ecs.ContainerImage.from_registry("envoyproxy/envoy:v1.28-latest"),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="envoy"),
            environment={
                "ENVOY_LOG_LEVEL": "info",
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:9901/ready || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
            ),
        )

        envoy_container.add_port_mappings(
            ecs.PortMapping(container_port=10000, protocol=ecs.Protocol.TCP)
        )

        # Authorizer Sidecar Container (Python FastAPI)
        authorizer_container = task_definition.add_container(
            "Authorizer",
            image=ecs.ContainerImage.from_asset(
                str(Path(__file__).parent.parent.parent / "lambda_handlers" / "authorizer")
            ),
            logging=ecs.LogDrivers.aws_logs(stream_prefix="authorizer"),
            environment={
                "JWT_SECRET_ARN": jwt_signing_secret.secret_arn,
            },
            health_check=ecs.HealthCheck(
                command=["CMD-SHELL", "curl -f http://localhost:9000/health || exit 1"],
                interval=Duration.seconds(30),
                timeout=Duration.seconds(5),
                retries=3,
            ),
        )

        authorizer_container.add_port_mappings(
            ecs.PortMapping(container_port=9000, protocol=ecs.Protocol.TCP)
        )

        # Application Load Balancer + Fargate Service
        alb_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "RajeeService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=2,
            public_load_balancer=True,
            listener_port=443,
            protocol=elbv2.ApplicationProtocol.HTTPS,
            # certificate=certificate,  # Add ACM certificate
        )

        # Health check on Envoy admin endpoint
        alb_service.target_group.configure_health_check(
            path="/ready",
            port="9901",
            interval=Duration.seconds(30),
            timeout=Duration.seconds(5),
            healthy_threshold_count=2,
            unhealthy_threshold_count=3,
        )

        # Auto-scaling
        scaling = alb_service.service.auto_scale_task_count(
            min_capacity=2,
            max_capacity=10,
        )

        scaling.scale_on_cpu_utilization(
            "CpuScaling",
            target_utilization_percent=70,
        )

        scaling.scale_on_request_count(
            "RequestScaling",
            requests_per_target=1000,
            target_group=alb_service.target_group,
        )

        CfnOutput(
            self,
            "TestBucketName",
            value=test_bucket.bucket_name,
            description="S3 bucket for RAJEE proxy testing",
        )

        self.load_balancer = alb_service.load_balancer
        self.service = alb_service.service
```

## End-to-End Flow

### 1. Client Gets Token (Control Plane)

```python
import requests

# Mint token with grants
response = requests.post(
    "https://raja-control-plane.example.com/token",
    json={
        "principal": "User::alice",
        "grants": [
            "s3:GetObject/my-bucket/",
            "s3:PutObject/my-bucket/uploads/"
        ],
        "ttl": 300  # 5 minutes
    }
)

token = response.json()["token"]
```

### 2. Client Uses boto3 with RAJEE Endpoint

```python
import boto3
from botocore.auth import SigV4Auth
from botocore.awsrequest import AWSRequest

# Create S3 client pointing to RAJEE proxy
s3_client = boto3.client(
    's3',
    endpoint_url='https://rajee.example.com',
    aws_access_key_id='dummy',      # Not used, but required by boto3
    aws_secret_access_key='dummy'
)

# Add JWT to request via event system
def add_jwt_header(request, **kwargs):
    request.headers['Authorization'] = f'Bearer {token}'

s3_client.meta.events.register('before-sign', add_jwt_header)

# Now use S3 API normally
response = s3_client.get_object(Bucket='my-bucket', Key='path/to/file.txt')
print(response['Body'].read())
```

### 3. Request Flow

1. **boto3** → Generates native S3 REST API request → `GET /my-bucket/path/to/file.txt`
2. **ALB** → Routes to Envoy Fargate task
3. **Envoy** → External auth filter calls authorizer sidecar
4. **Authorizer** →
   - Extracts JWT from `Authorization: Bearer <token>` header
   - Verifies signature
   - Constructs request: `s3:GetObject/my-bucket/path/to/file.txt`
   - Checks: `"s3:GetObject/my-bucket/path/to/file.txt".startswith("s3:GetObject/my-bucket/")` ✅
   - Returns: `{"result": {"allowed": true}}`
5. **Envoy** → Forwards request to S3 upstream (unmodified)
6. **S3** → Returns object
7. **Envoy** → Streams response back to client
8. **boto3** → Returns response to application

## Advantages

### 1. True S3 Compatibility

- boto3 works out of the box ✅
- All S3 operations supported (GET, PUT, DELETE, LIST, multipart, etc.) ✅
- No custom API to learn ✅

### 2. Zero S3 Knowledge in Proxy

- Envoy doesn't parse S3 operations ✅
- Authorizer only extracts action/bucket/key ✅
- S3 complexity handled by AWS ✅

### 3. Streaming and Performance

- No Lambda 6MB response limit ✅
- True HTTP streaming ✅
- Low latency (Envoy + sidecar in same task) ✅

### 4. Fail-Closed Security

- Envoy blocks requests if authorizer returns deny ✅
- Authorizer fails closed on errors ✅
- No direct S3 access bypass ✅

### 5. Scalability

- ECS auto-scaling based on load ✅
- Envoy connection pooling to S3 ✅
- Authorizer collocated (low latency) ✅

## Testing Strategy

### Unit Tests

1. **Request String Construction** (`tests/unit/test_request_string.py`)
   ```python
   def test_get_object_request():
       assert construct_request_string("GET", "/bucket/key", {}) == "s3:GetObject/bucket/key"

   def test_list_bucket_request():
       assert construct_request_string("GET", "/bucket", {"list-type": "2"}) == "s3:ListBucket/bucket/"
   ```

2. **Prefix Authorization** (`tests/unit/test_prefix_authz.py`)
   ```python
   def test_prefix_match():
       grants = ["s3:GetObject/bucket/uploads/"]
       assert is_authorized("s3:GetObject/bucket/uploads/file.txt", grants)

   def test_prefix_no_match():
       grants = ["s3:GetObject/bucket/uploads/"]
       assert not is_authorized("s3:GetObject/bucket/docs/file.txt", grants)
   ```

### Integration Tests

1. **End-to-End S3 Operations** (`tests/integration/test_rajee_envoy_e2e.py`)
   ```python
   def test_authorized_get_object():
       # 1. Mint token
       token = mint_token(grants=["s3:GetObject/test-bucket/"])

       # 2. Create S3 client pointing to RAJEE
       s3 = boto3.client('s3', endpoint_url=RAJEE_ENDPOINT)
       s3.meta.events.register('before-sign', lambda req, **kw: req.headers.update({'Authorization': f'Bearer {token}'}))

       # 3. Upload test object directly to S3
       real_s3 = boto3.client('s3')
       real_s3.put_object(Bucket='test-bucket', Key='test.txt', Body=b'content')

       # 4. Read via RAJEE
       response = s3.get_object(Bucket='test-bucket', Key='test.txt')
       assert response['Body'].read() == b'content'

   def test_denied_put_object():
       # Token only has GetObject, try PutObject
       token = mint_token(grants=["s3:GetObject/test-bucket/"])

       s3 = boto3.client('s3', endpoint_url=RAJEE_ENDPOINT)
       s3.meta.events.register('before-sign', lambda req, **kw: req.headers.update({'Authorization': f'Bearer {token}'}))

       # Should fail with 403
       with pytest.raises(ClientError) as exc:
           s3.put_object(Bucket='test-bucket', Key='test.txt', Body=b'content')

       assert exc.value.response['Error']['Code'] == 'Forbidden'
   ```

2. **Performance Tests** (`tests/integration/test_authorizer_performance.py`)
   - Measure end-to-end latency
   - Verify no policy/database calls
   - Load testing with multiple concurrent requests

## Security Considerations

### 1. JWT Security

- **Secret storage**: Secrets Manager
- **Token expiration**: Short TTL (5 minutes default)
- **Signature verification**: Every request
- **Algorithm**: HS256 (symmetric key)

### 2. Network Security

- **Private subnets**: Envoy tasks in private subnets
- **Security groups**: Only ALB can reach Envoy
- **TLS**: ALB terminates TLS
- **VPC endpoints**: Optional S3 VPC endpoint

### 3. Authorization Security

- **Fail-closed**: Authorizer errors result in DENY
- **No bypass**: All S3 access through Envoy
- **Prefix validation**: Trailing slash enforcement
- **Audit logging**: CloudWatch Logs for all decisions

### 4. S3 Access

- **IAM role**: ECS task role with S3 permissions
- **Bucket policies**: Optional additional restrictions
- **No public access**: Buckets must block public access

## Implementation Tasks

### Phase 1: Authorizer Service (2 days)

1. Create authorizer FastAPI service
2. Implement request string construction
3. Implement prefix authorization
4. Add unit tests
5. Dockerize authorizer

### Phase 2: Envoy Configuration (1 day)

1. Create envoy.yaml configuration
2. Configure ext_authz filter
3. Configure S3 upstream cluster
4. Test locally with Docker Compose

### Phase 3: CDK Infrastructure (2 days)

1. Create ECS Fargate stack
2. Create ALB + target group
3. Configure task definition (Envoy + Authorizer)
4. Add auto-scaling policies
5. Deploy to AWS

### Phase 4: Integration Testing (1 day)

1. Create boto3 test harness
2. Test all S3 operations (GET, PUT, DELETE, LIST)
3. Test authorization (allowed/denied scenarios)
4. Performance testing

### Phase 5: Documentation (0.5 days)

1. User guide for boto3 integration
2. Deployment guide
3. Troubleshooting guide

**Total: 6-7 days**

## Comparison with Previous Design

| Aspect | API Gateway + Lambda | Envoy Proxy |
|--------|---------------------|-------------|
| S3 Compatibility | ❌ Custom API | ✅ Native S3 API |
| boto3 Support | ❌ No | ✅ Yes |
| S3 Operations | ❌ Manual implementation | ✅ All operations |
| Streaming | ❌ 6MB limit | ✅ Unlimited |
| Latency | ~100ms (Lambda cold start) | ~10ms (sidecar) |
| Complexity | High (reimpl S3 API) | Low (pure proxy) |
| Maintainability | ❌ Must update for new S3 features | ✅ Zero S3 knowledge |

## Success Criteria

- [ ] boto3 client can point to RAJEE endpoint
- [ ] All S3 operations work (GET, PUT, DELETE, LIST)
- [ ] Authorization enforced on every request
- [ ] Prefix-based authorization logic works correctly
- [ ] No policy/database calls on hot path
- [ ] Authorizer latency < 10ms (sidecar) or < 50ms (Lambda)
- [ ] Fail-closed behavior verified
- [ ] 100% test coverage for authorization logic

## Conclusion

**Envoy-based RAJEE is the correct architecture** for building a transparent S3 proxy with prefix-based authorization.

Key advantages:
1. ✅ Native S3 API compatibility (boto3 works)
2. ✅ Zero S3 implementation (pure proxy)
3. ✅ Streaming support (no size limits)
4. ✅ Low latency (sidecar authorizer)
5. ✅ Production-ready (Envoy is battle-tested)

This design delivers on the original vision: **a transparent S3 proxy that enforces prefix-based authorization using JWT tokens**.

## Next Steps

1. Review this design with team
2. Begin Phase 1: Build authorizer service
3. Prototype Envoy config locally
4. Deploy to AWS and validate
5. Write comprehensive tests

## References

- [Envoy External Authorization](https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_authz_filter)
- [S3 REST API Reference](https://docs.aws.amazon.com/AmazonS3/latest/API/Welcome.html)
- [01-rajee-gap.md](01-rajee-gap.md) - Gap analysis
- [02-prefix-authorization-design.md](02-prefix-authorization-design.md) - Prefix matching design
