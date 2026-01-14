# raja
![CI](https://github.com/quiltdata/raja/workflows/CI/badge.svg)
![Integration Tests](https://github.com/quiltdata/raja/workflows/Integration%20Tests/badge.svg)
![Coverage](https://codecov.io/gh/quiltdata/raja/branch/main/graph/badge.svg)

Resource Authorization JWT Authority for Software-Defined Authorization

## Overview

RAJA (Resource Authorization with JWT and Scopes) is an implementation of the Scope-based Distributed Authorization (SDA) pattern. It compiles Cedar policies into JWT token scopes for deterministic, transparent authorization decisions.

### Key Features

- **Cedar Policy Compilation**: Convert Cedar policies to explicit JWT scopes
- **Deterministic Authorization**: Same token + same request = same decision, always
- **Transparent**: Token inspection reveals exact authorities granted
- **Fail-Closed**: Unknown requests are explicitly denied
- **AWS Integration**: Built on AWS Verified Permissions, Lambda, and DynamoDB

## Quick Start

### Installation

```bash
# Clone repository
git clone https://github.com/quiltdata/raja.git
cd raja

# Install dependencies with UV
uv sync
```

### Deploy Infrastructure

```bash
# Deploy AWS infrastructure
poe cdk deploy --all

# Load Cedar policies
python scripts/load_policies.py

# Compile policies to scopes
python scripts/invoke_compiler.py
```

### Access Web Interface

After deployment, find the CloudFront URL in the CDK outputs:

```bash
poe cdk deploy RajaWebStack --outputs-file outputs.json
```

Open the CloudFront URL in your browser to access the interactive demo interface.

## Web Interface

RAJA includes a web-based demo interface for interactive exploration:

### Features

- **Token Issuance Panel**: Request JWT tokens for different principals (alice, bob, admin)
- **Authorization Testing Panel**: Test ALLOW/DENY decisions with tokens and requests
- **Token Introspection Panel**: Decode and inspect JWT token claims

### Usage

1. **Request a Token**: Enter a principal name and click "Request Token"
2. **Test Authorization**: Paste the token and test resource access
3. **Inspect Token**: View decoded claims to verify scopes

### Example Workflow

```text
1. Request token for "alice"
   → Receives JWT with scopes: ["Document:doc123:read", "Document:doc123:write"]

2. Test authorization: Document:doc123:read
   → Result: ALLOWED (scope found in token)

3. Test authorization: Document:doc456:read
   → Result: DENIED (scope not found in token)

4. Introspect token
   → View claims: {sub: "alice", scopes: [...], exp: 1234567890}
```

See [web/README.md](web/README.md) for detailed web interface documentation.

## Architecture

### Components

```text
┌─────────────────┐
│ Cedar Policies  │ (AWS Verified Permissions)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Policy Compiler │ (Lambda + DynamoDB)
└────────┬────────┘
         │
         ▼
┌─────────────────┐       ┌──────────────┐
│ Token Service   │──────→│ JWT Tokens   │
└─────────────────┘       └──────┬───────┘
                                 │
                                 ▼
                          ┌──────────────┐
                          │  Enforcer    │
                          └──────────────┘
```

### AWS Services

- **Amazon Verified Permissions (AVP)**: Cedar policy storage and validation
- **AWS Lambda**: Policy compiler, token service, enforcer, introspection
- **Amazon DynamoDB**: Cached policy-to-scope mappings
- **Amazon API Gateway**: REST API endpoints
- **AWS Secrets Manager**: JWT signing key storage
- **Amazon S3 + CloudFront**: Static web interface hosting

## API Endpoints

### POST /token

Issue a JWT token for a principal.

**Request:**

```json
{
  "principal": "alice"
}
```

**Response:**

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "principal": "alice",
  "scopes": [
    "Document:doc123:read",
    "Document:doc123:write"
  ]
}
```

### POST /authorize

Check authorization for a request.

**Request:**

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "request": {
    "resource_type": "Document",
    "resource_id": "doc123",
    "action": "read"
  }
}
```

**Response:**

```json
{
  "allowed": true,
  "reason": "Scope found in token",
  "matched_scope": "Document:doc123:read"
}
```

### GET /introspect

Decode and inspect a JWT token.

**Request:**

```text
GET /introspect?token=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Response:**

```json
{
  "claims": {
    "sub": "alice",
    "scopes": ["Document:doc123:read"],
    "iat": 1234567890,
    "exp": 1234571490
  }
}
```

## Development

### Running Tests

```bash
# Unit tests (no AWS required)
poe test-unit

# Integration tests (requires deployed infrastructure)
poe test-integration

# All tests
poe test
```

### Local Development

The `raja` Python package can be used independently of AWS:

```python
from raja import compile_policy, create_token, enforce

# Compile Cedar policy to scopes
scopes = compile_policy(cedar_policy_string)

# Create JWT token
token = create_token(subject="alice", scopes=scopes, ttl=3600, secret="secret")

# Enforce authorization
decision = enforce(token, request, secret="secret")
print(decision.allowed)  # True or False
```

### Cedar Policies

Sample policies are in [policies/](policies/):

- `document_read.cedar` - Read access to specific documents
- `document_write.cedar` - Write access to specific documents
- `admin_full.cedar` - Full admin access

Edit these policies and redeploy to test different authorization scenarios.

## Project Structure

```text
raja/
├── src/raja/              # Core Python library
│   ├── models.py          # Data models (Pydantic)
│   ├── token.py           # JWT operations
│   ├── compiler.py        # Cedar → Scopes compiler
│   ├── enforcer.py        # Authorization enforcement
│   └── cedar/             # Cedar policy parsing
├── lambda_handlers/       # AWS Lambda functions
│   ├── compiler/          # Policy compilation Lambda
│   ├── token_service/     # Token issuance Lambda
│   ├── enforcer/          # Authorization Lambda
│   └── introspect/        # Token introspection Lambda
├── infra/raja_poc/        # AWS CDK infrastructure
│   ├── stacks/            # CDK stacks
│   └── constructs/        # Reusable constructs
├── web/                   # Web interface (S3 + CloudFront)
│   ├── index.html         # Main UI
│   ├── app.js             # Frontend logic
│   └── styles.css         # Styling
├── policies/              # Sample Cedar policies
└── tests/                 # Test suite

```

## RAJA Hypotheses

The RAJA MVP validates four core hypotheses of the SDA pattern:

1. **Determinism**: Same token + same request → same decision, always
2. **Compilation**: Cedar policies compile to explicit, inspectable scopes
3. **Fail-Closed**: Unknown requests are explicitly denied (no default allow)
4. **Transparency**: Token inspection reveals exact authorities granted

All hypotheses are validated through the test suite in [tests/hypothesis/](tests/hypothesis/).

## Contributing

See [specs/1-mvp/](specs/1-mvp/) for design documentation.

## License

[License information to be added]
