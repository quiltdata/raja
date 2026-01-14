# RAJA Infrastructure

## Overview

The `infra/` directory contains AWS CDK infrastructure for deploying RAJA as a managed service. The infrastructure is **optional** - the core `raja` library can be used standalone without AWS.

## Architecture

```
┌──────────────────┐
│  API Gateway     │  (REST API)
└────────┬─────────┘
         │
    ┌────┴────┬────────────┬─────────────┐
    ▼         ▼            ▼             ▼
┌────────┐ ┌────────┐ ┌──────────┐ ┌──────────┐
│ Token  │ │ Enforce│ │ Compiler │ │Introspect│ (Lambda Functions)
│Service │ │  API   │ │  Lambda  │ │  Lambda  │
└───┬────┘ └────┬───┘ └─────┬────┘ └──────────┘
    │           │            │
    ▼           ▼            ▼
┌────────────────────────────────┐
│         DynamoDB Tables        │
│  - PolicyScopeMappings         │
│  - PrincipalScopes             │
└────────────────────────────────┘
         │
         ▼
┌────────────────────────────────┐
│    Secrets Manager             │
│  - JWT Signing Key             │
└────────────────────────────────┘
         │
         ▼
┌────────────────────────────────┐
│  Amazon Verified Permissions   │
│  - Cedar Policy Store          │
│  - Cedar Schema                │
└────────────────────────────────┘
```

## Directory Structure

```
infra/
├── cdk.json                          # CDK app configuration
├── requirements.txt                  # Python dependencies for CDK
├── requirements-dev.txt              # Development dependencies
└── raja_poc/
    ├── __init__.py
    ├── app.py                        # CDK app entry point
    ├── stacks/
    │   ├── __init__.py
    │   ├── avp_stack.py              # AVP policy store stack
    │   └── services_stack.py         # API, Lambda, DynamoDB stack
    └── constructs/
        ├── __init__.py
        ├── compiler_lambda.py        # Compiler Lambda construct
        ├── enforcer_lambda.py        # Enforcer Lambda construct
        ├── token_service.py          # Token service Lambda construct
        ├── introspect_lambda.py      # Token introspection construct
        └── policy_store.py           # AVP policy store construct
```

## CDK Stacks

### 1. AVP Stack (`stacks/avp_stack.py`)

Creates Amazon Verified Permissions resources:

**Resources:**

- **Policy Store** - Stores Cedar policies and schema
- **Schema** - Defines entities and actions (from `policies/schema.cedar`)

**Outputs:**

- `PolicyStoreId` - Policy store identifier for Lambda functions

**Purpose:**

- Central repository for Cedar policies
- Schema validation for policies
- Policy versioning and management

### 2. Services Stack (`stacks/services_stack.py`)

Main application stack with API and Lambda functions:

**Resources:**

1. **DynamoDB Tables**
   - `PolicyScopeMappings` - Maps policy IDs to compiled scopes
   - `PrincipalScopes` - Maps principals to their granted scopes
   - Both with on-demand billing and point-in-time recovery

2. **Secrets Manager**
   - `JWTSigningKey` - JWT signing secret (auto-generated)
   - Rotated via Lambda (optional future enhancement)

3. **Lambda Functions**
   - Compiler - Compile Cedar policies to scopes
   - Enforcer - Validate tokens and check authorization
   - Token Service - Issue JWT tokens
   - Introspect - Decode and return token claims

4. **API Gateway**
   - REST API with CloudWatch logging
   - CORS enabled for development
   - Endpoints:
     - `POST /token` → Token Service
     - `POST /authorize` → Enforcer
     - `POST /compile` → Compiler (admin only)
     - `GET /introspect` → Introspect
     - `GET /health` → Health check

**Outputs:**

- `ApiUrl` - API Gateway endpoint URL
- `MappingsTableName` - PolicyScopeMappings table name
- `PrincipalTableName` - PrincipalScopes table name
- `JWTSecretArn` - JWT signing key ARN

## CDK Constructs

### 1. Compiler Lambda (`constructs/compiler_lambda.py`)

**Purpose:** Compile Cedar policies from AVP to scope strings

**Handler:** `lambda_handlers/compiler/handler.py`

**Environment Variables:**

- `POLICY_STORE_ID` - AVP policy store ID
- `MAPPINGS_TABLE` - DynamoDB table for policy mappings
- `PRINCIPAL_TABLE` - DynamoDB table for principal mappings

**IAM Permissions:**

- `verifiedpermissions:ListPolicies` - Read policies from AVP
- `verifiedpermissions:GetPolicy` - Get policy details
- `dynamodb:PutItem` - Write compiled scopes to DynamoDB

**Triggers:**

- EventBridge rule (periodic compilation, e.g., every 5 minutes)
- Manual invocation via API or CLI

**Process:**

1. Fetch all policies from AVP policy store
2. For each policy, call `raja.compile_policy()`
3. Store policy_id → scopes mapping in `PolicyScopeMappings` table
4. Store principal → scopes mapping in `PrincipalScopes` table
5. Log compilation results

### 2. Token Service Lambda (`constructs/token_service.py`)

**Purpose:** Issue JWT tokens with scopes for principals

**Handler:** `lambda_handlers/token_service/handler.py`

**Environment Variables:**

- `PRINCIPAL_TABLE` - DynamoDB table with principal scopes
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT secret

**IAM Permissions:**

- `dynamodb:GetItem` - Read principal scopes from DynamoDB
- `secretsmanager:GetSecretValue` - Read JWT signing secret

**API Endpoint:** `POST /token`

**Request Body:**

```json
{
  "principal": "User::alice"
}
```

**Response:**

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "principal": "User::alice",
  "scopes": ["Document:doc123:read", "Document:doc456:write"],
  "expires_in": 3600
}
```

**Process:**

1. Extract principal from request
2. Query `PrincipalScopes` table for principal's scopes
3. Retrieve JWT signing secret from Secrets Manager
4. Call `raja.create_token()` with principal and scopes
5. Return token with metadata

### 3. Enforcer Lambda (`constructs/enforcer_lambda.py`)

**Purpose:** Validate tokens and check authorization via subset checking

**Handler:** `lambda_handlers/enforcer/handler.py`

**Environment Variables:**

- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT secret

**IAM Permissions:**

- `secretsmanager:GetSecretValue` - Read JWT signing secret

**API Endpoint:** `POST /authorize`

**Request Body:**

```json
{
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "resource": "Document::doc123",
  "action": "read"
}
```

**Response:**

```json
{
  "decision": "ALLOW",
  "reason": "Requested scope Document:doc123:read is covered by granted scope Document:*:read",
  "requested_scope": "Document:doc123:read",
  "granted_scopes": ["Document:*:read", "Document:doc456:write"]
}
```

**Process:**

1. Extract token, resource, action from request
2. Retrieve JWT signing secret from Secrets Manager
3. Call `raja.enforce()` with token, resource, action
4. Return authorization decision

**Performance:**

- No DynamoDB queries - all data in token
- No policy evaluation - pure subset checking
- Sub-100ms latency typical

### 4. Introspect Lambda (`constructs/introspect_lambda.py`)

**Purpose:** Decode JWT tokens and return claims (for debugging/monitoring)

**Handler:** `lambda_handlers/introspect/handler.py`

**Environment Variables:**

- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT secret

**IAM Permissions:**

- `secretsmanager:GetSecretValue` - Read JWT signing secret

**API Endpoint:** `GET /introspect`

**Request Headers:**

```
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

**Response:**

```json
{
  "principal": "User::alice",
  "scopes": ["Document:doc123:read", "Document:doc456:write"],
  "iat": 1704067200,
  "exp": 1704070800,
  "active": true
}
```

**Use Cases:**

- Debugging token issues
- Monitoring token usage
- Auditing token contents

### 5. Policy Store (`constructs/policy_store.py`)

**Purpose:** Manage AVP policy store and schema

**Resources:**

- CfnPolicyStore - AVP policy store
- CfnSchema - Cedar schema (from `policies/schema.cedar`)

**Configuration:**

- Validation mode: STRICT (enforce schema compliance)
- Description: Metadata about policy store

**Management:**

- Policies loaded via `scripts/load_policies.py`
- Schema updates via CDK deployment

## Deployment

### Prerequisites

```bash
# Install Node.js (for CDK)
brew install node  # macOS
# or
apt-get install nodejs  # Linux

# Install AWS CDK globally
npm install -g aws-cdk

# Install Python dependencies
pip install -r infra/requirements.txt

# Configure AWS credentials
aws configure
```

### Deploy Infrastructure

```bash
# Quick deployment (uses helper script)
./scripts/deploy.sh

# Manual deployment
cd infra
cdk synth      # Synthesize CloudFormation template
cdk deploy     # Deploy to AWS
```

### Load Policies

```bash
# Load Cedar policies to AVP
python scripts/load_policies.py

# Trigger policy compilation
python scripts/invoke_compiler.py
```

### Verify Deployment

```bash
# Get API URL from stack outputs
aws cloudformation describe-stacks \
  --stack-name RajaPocServicesStack \
  --query 'Stacks[0].Outputs[?OutputKey==`ApiUrl`].OutputValue' \
  --output text

# Health check
curl https://<api-url>/health

# Request token
curl -X POST https://<api-url>/token \
  -H "Content-Type: application/json" \
  -d '{"principal": "User::alice"}'

# Check authorization
curl -X POST https://<api-url>/authorize \
  -H "Content-Type: application/json" \
  -d '{
    "token": "<token-from-above>",
    "resource": "Document::doc123",
    "action": "read"
  }'
```

## Configuration

### CDK Context (`cdk.json`)

```json
{
  "app": "python3 raja_poc/app.py",
  "context": {
    "@aws-cdk/core:enableStackNameDuplicates": true,
    "aws-cdk:enableDiffNoFail": true
  }
}
```

### Environment Variables

Lambda functions use these environment variables:

- `POLICY_STORE_ID` - AVP policy store ID (from AVP stack output)
- `MAPPINGS_TABLE` - PolicyScopeMappings table name
- `PRINCIPAL_TABLE` - PrincipalScopes table name
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT secret
- `LOG_LEVEL` - Logging level (default: INFO)

### CDK Synthesis

```bash
# Synthesize CloudFormation template
cdk synth

# View synthesized template
cat cdk.out/RajaPocServicesStack.template.json
```

## Cost Considerations

Typical monthly costs (low traffic):

- **API Gateway:** ~$3.50 per million requests
- **Lambda:** Free tier (1M requests, 400K GB-seconds)
- **DynamoDB:** ~$1.25 per million writes (on-demand)
- **Secrets Manager:** ~$0.40 per secret per month
- **AVP:** ~$15 per million authorization requests (if using AVP API)

**RAJA Advantage:** Enforcement uses Lambda + DynamoDB, not AVP API, so authorization is ~10x cheaper.

## Monitoring

### CloudWatch Metrics

- **API Gateway:**
  - Request count
  - Latency (p50, p99)
  - Error rates (4xx, 5xx)

- **Lambda:**
  - Invocation count
  - Duration
  - Error count
  - Throttles

- **DynamoDB:**
  - Read/write capacity
  - Throttled requests
  - User errors

### CloudWatch Logs

- **API Gateway:** Access logs and execution logs
- **Lambda:** Function logs (JSON structured logging)
- **DynamoDB:** Stream events (if enabled)

### Alarms (Recommended)

- API Gateway 5xx errors > 5%
- Lambda error rate > 1%
- Lambda duration > 3 seconds (p99)
- DynamoDB throttled requests > 10

## Security

### IAM Policies

Lambda functions follow least-privilege principle:

- Compiler: Read AVP + Write DynamoDB
- Token Service: Read DynamoDB + Read Secrets
- Enforcer: Read Secrets only
- Introspect: Read Secrets only

### Secrets Management

- JWT signing key stored in Secrets Manager
- Auto-generated on first deployment
- Can be rotated via Secrets Manager rotation Lambda (future)

### API Authentication

Current: Public API (for POC)
Production: Add API Gateway authorizer (Cognito or custom Lambda)

### Network Security

Current: Public subnets (for POC)
Production: Private subnets + VPC endpoints for AWS services

## Troubleshooting

### Deployment Fails

```bash
# Check CDK bootstrap
cdk bootstrap aws://ACCOUNT-ID/REGION

# Check IAM permissions
aws sts get-caller-identity

# View detailed errors
cdk deploy --verbose
```

### Lambda Errors

```bash
# View Lambda logs
aws logs tail /aws/lambda/RajaPocCompilerLambda --follow

# Invoke Lambda directly for testing
aws lambda invoke \
  --function-name RajaPocCompilerLambda \
  --payload '{}' \
  response.json
```

### DynamoDB Issues

```bash
# Check table status
aws dynamodb describe-table --table-name PolicyScopeMappings

# Scan table contents
aws dynamodb scan --table-name PrincipalScopes
```

### AVP Issues

```bash
# List policy stores
aws verifiedpermissions list-policy-stores

# List policies
aws verifiedpermissions list-policies --policy-store-id <id>

# Get policy details
aws verifiedpermissions get-policy --policy-id <id> --policy-store-id <store-id>
```

## Cleanup

```bash
# Destroy all resources
cd infra
cdk destroy

# Or use helper script
./scripts/deploy.sh --destroy
```

**Note:** This deletes all DynamoDB tables, policies, and configurations. Backup important data first.

## Future Enhancements

- **Multi-region deployment** - Active-active for HA
- **DynamoDB streams** - Real-time policy updates
- **Custom authorizer** - API Gateway authorization
- **VPC integration** - Private subnets for Lambda
- **X-Ray tracing** - Distributed tracing
- **Backup automation** - DynamoDB backups via AWS Backup
- **Cost optimization** - Reserved capacity for predictable workloads
