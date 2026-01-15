# RAJA Infrastructure

## Overview

The `infra/` directory contains AWS CDK infrastructure for deploying RAJA as a managed service. The infrastructure is **optional** - the core `raja` library can be used standalone without AWS.

## Architecture

```
┌──────────────────┐
│  API Gateway     │  (REST API)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Control Plane    │  (FastAPI + Mangum)
│ Lambda           │
└────────┬─────────┘
         │
         ▼
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
        ├── control_plane.py          # FastAPI control plane Lambda
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
   - `HarnessSigningKey` - S3 harness signing secret (auto-generated)
   - Rotated via Lambda (optional future enhancement)

3. **Lambda Function**
   - Control Plane - FastAPI app for compile/token/policy/principal operations

4. **API Gateway**
   - REST API with CloudWatch logging
   - CORS enabled for development
   - Proxy routing to the FastAPI control plane

**Outputs:**

- `ApiUrl` - API Gateway endpoint URL
- `MappingsTableName` - PolicyScopeMappings table name
- `PrincipalTableName` - PrincipalScopes table name
- `JWTSecretArn` - JWT signing key ARN
- `HarnessSecretArn` - S3 harness signing key ARN
- `ControlPlaneLambdaArn` - Control plane Lambda ARN

### 3. Rajee Envoy Stack (`stacks/rajee_envoy_stack.py`)

ECS Fargate deployment for the RAJEE Envoy S3 proxy with a FastAPI authorizer
sidecar. Uses an Application Load Balancer with optional TLS (ACM certificate ARN).

**Resources:**

1. **VPC + ECS Cluster**
2. **Fargate Service**
   - Envoy proxy container (custom config image)
   - Authorizer sidecar (FastAPI)
3. **ALB + Target Group**
   - Health checks against Envoy admin port
4. **Auto-scaling**

**Inputs:**

- `jwt_signing_secret` - Secrets Manager secret for JWT validation
- `certificate_arn` (optional) - ACM certificate ARN for HTTPS

## CDK Constructs

### Control Plane Lambda (`constructs/control_plane.py`)

**Purpose:** Serve the FastAPI control plane via API Gateway.

**Handler:** `lambda_handlers/control_plane/handler.py`

**Environment Variables:**

- `POLICY_STORE_ID` - AVP policy store ID
- `MAPPINGS_TABLE` - DynamoDB table for policy mappings
- `PRINCIPAL_TABLE` - DynamoDB table for principal mappings
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT secret
- `HARNESS_SECRET_ARN` - Secrets Manager ARN for S3 harness secret

**IAM Permissions:**

- `verifiedpermissions:ListPolicies`, `verifiedpermissions:GetPolicy`
- `dynamodb:PutItem`, `dynamodb:GetItem`, `dynamodb:Scan`
- `secretsmanager:GetSecretValue` (for both JWT and harness secrets)

### Policy Store (`constructs/policy_store.py`)

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

## Local Testing

Before deploying to AWS, test Docker containers locally to validate health checks and configuration.

### Quick Start

```bash
# From repo root
./poe test-docker

# Or directly from infra directory
cd infra
./test-docker.sh
```

### Available Commands

```bash
# Start containers (default)
./poe test-docker
./test-docker.sh up

# View logs
ARGS=logs ./poe test-docker
./test-docker.sh logs
./test-docker.sh logs envoy      # Only Envoy logs
./test-docker.sh logs authorizer # Only authorizer logs

# Stop containers
ARGS=down ./poe test-docker
./test-docker.sh down

# Check status
./test-docker.sh status
```

### What Gets Tested

The local Docker environment validates:
- Container build processes
- Health check endpoints (Envoy `/ready`, Authorizer `/docs`)
- Inter-container communication (Envoy → Authorizer)
- Envoy configuration
- Authorizer FastAPI app startup

### Architecture

```
┌─────────────────────────────────────────┐
│  Docker Compose Network (rajee-net)     │
│                                         │
│  ┌──────────────┐    ┌──────────────┐  │
│  │ Authorizer   │    │   Envoy      │  │
│  │ (FastAPI)    │◄───│   Proxy      │  │
│  │              │    │              │  │
│  │ Port: 9000   │    │ Port: 10000  │  │
│  └──────────────┘    │ Admin: 9901  │  │
│                      └──────────────┘  │
└─────────────────────────────────────────┘
           ▲
           │
     localhost:10000
```

**Available endpoints:**
- Envoy Proxy: `http://localhost:10000`
- Envoy Admin: `http://localhost:9901`
- Authorizer API docs: `http://localhost:9000/docs`

### Differences from AWS

Local testing uses simplified configuration:
1. **JWT Secret:** Hardcoded test secret (not Secrets Manager)
2. **S3 Access:** No IAM role credentials
3. **Networking:** Docker bridge network (not VPC)
4. **Load Balancer:** Direct Envoy access (no ALB)

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

# Optional: Test containers locally first
./poe test-docker
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

# Compile policies
curl -X POST https://<api-url>/compile

# Request token
curl -X POST https://<api-url>/token \
  -H "Content-Type: application/json" \
  -d '{"principal": "User::alice"}'

# List principals
curl https://<api-url>/principals
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
- `HARNESS_SECRET_ARN` - Secrets Manager ARN for S3 harness secret
- `LOG_LEVEL` - Logging level (default: INFO)

For local development, you can use the `RAJ_HARNESS_SECRET` environment variable instead of `HARNESS_SECRET_ARN` to provide the harness signing key directly without AWS Secrets Manager.

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

**RAJA Advantage:** Enforcement is library-first (no service calls) and only control-plane operations touch AWS APIs.

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

- Control plane: Read AVP + Read/Write DynamoDB + Read Secrets

### Secrets Management

- JWT signing key stored in Secrets Manager (auto-generated on first deployment)
- S3 harness signing key stored in Secrets Manager (auto-generated on first deployment)
- Both secrets are automatically generated with secure random values
- Can be rotated via Secrets Manager rotation Lambda (future enhancement)
- For local development, use `RAJ_HARNESS_SECRET` environment variable to bypass Secrets Manager

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
