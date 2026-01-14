# RAJA Lambda Handlers

## Overview

The `lambda_handlers/` directory contains the Lambda entrypoint for the FastAPI control plane.
All enforcement happens locally in the `raja` library; the control plane handles admin actions
like compiling policies and issuing tokens.

## Directory Structure

```
lambda_handlers/
├── __init__.py
└── control_plane/
    ├── handler.py              # Mangum adapter for FastAPI app
    └── requirements.txt        # FastAPI + Mangum dependencies
```

## Control Plane Handler (`control_plane/handler.py`)

**Purpose:** Expose the FastAPI control plane via API Gateway.

**Entrypoint:** `handler.handler` (Mangum wraps the FastAPI app)

**Environment Variables:**

- `POLICY_STORE_ID` - AVP policy store identifier
- `MAPPINGS_TABLE` - DynamoDB table for policy → scopes mappings
- `PRINCIPAL_TABLE` - DynamoDB table for principal → scopes mappings
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT secret
- `TOKEN_TTL` - Token TTL in seconds (default: 3600)

**Dependencies:**

- `fastapi`
- `mangum`
