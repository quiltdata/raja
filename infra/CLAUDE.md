# RAJA Infrastructure

## Overview

The `infra/` directory contains infrastructure for deploying RAJA as a managed AWS service.

**Primary:** `infra/terraform/` — Terraform (current, use this)
**Supporting assets:** `infra/envoy/` and `infra/layers/`

The infrastructure is **optional** — the core `raja` library can be used standalone without AWS.

## Architecture

```
┌──────────────────┐
│  API Gateway     │  (REST API — control plane)
└────────┬─────────┘
         │
         ▼
┌──────────────────┐
│ Control Plane    │  (FastAPI + Mangum Lambda)
│ Lambda           │
└────────┬─────────┘
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
         │
         ▼
┌────────────────────────────────┐
│  ECS Fargate (RAJEE / RALE)    │
│  - Envoy proxy (RALE routing)  │
│  - ALB + target groups         │
└────────────────────────────────┘
```

## Directory Structure

```
infra/
├── terraform/           # ✅ PRIMARY — use this
│   ├── main.tf          # All AWS resources
│   ├── outputs.tf       # Stack outputs
│   ├── variables.tf     # Input variables
│   └── versions.tf      # Provider version constraints
│
├── envoy/               # Envoy proxy image assets
├── layers/              # Shared Lambda layer requirements
└── docker-compose.yml   # Local Envoy testing
```

## Terraform Resources

### Core (Control Plane)

| Resource | Description |
| --- | --- |
| `aws_api_gateway_rest_api` | REST API for control plane |
| `aws_lambda_function.control_plane` | FastAPI control plane |
| `aws_secretsmanager_secret.jwt` | JWT signing key |
| `aws_verifiedpermissions_policy_store` | Cedar policy store + schema |

### RALE Stack

| Resource | Description |
| --- | --- |
| `aws_lambda_function.rale_authorizer` | Issues TAJ tokens (cached) |
| `aws_lambda_function.rale_router` | Resolves USL → S3, streams object |
| `aws_lb.rajee` | ALB for Envoy proxy |
| `aws_ecs_service` | Fargate Envoy service |
| `aws_ecr_repository.envoy` | Envoy Docker image registry |
| `aws_s3_bucket.rajee_test` | Integration test bucket |

### Key Outputs

```bash
terraform output api_url              # Control plane API Gateway URL
terraform output rajee_endpoint       # RAJEE/RALE Envoy ALB URL
terraform output rale_authorizer_url  # RALE authorizer Lambda URL
terraform output rale_router_url      # RALE router Lambda URL
terraform output jwt_secret_arn       # JWT signing key ARN
terraform output rajee_test_bucket_name
```

## Deployment

### Prerequisites

```bash
# Terraform >= 1.5
brew install terraform   # macOS
terraform --version

# AWS credentials
aws configure
aws sts get-caller-identity
```

### Deploy

```bash
# From repo root (recommended)
./poe deploy

# Or directly
cd infra/terraform
terraform init
terraform apply
```

### Preview changes

```bash
cd infra/terraform && terraform plan
```

### Destroy

```bash
# From repo root
./poe destroy

# Or directly
cd infra/terraform && terraform destroy
```

## Local Docker Testing

Test the Envoy container locally before deploying:

```bash
# From repo root
./poe test-docker

# Or from infra/
cd infra && ./test-docker.sh
```

**Available endpoints:**
- Envoy Proxy: `http://localhost:10000`
- Envoy Admin: `http://localhost:9901`

## Configuration

### Terraform Variables (`variables.tf`)

Key variables (with defaults):

- `aws_region` — AWS region (default: `us-east-1`)
- `environment` — Environment name (default: `poc`)
- `ecs_cpu_architecture` — `X86_64` or `ARM64` (default: `ARM64`)
- `admin_allowed_cidrs` — CIDRs for Envoy admin UI (default: `[]`, disabled)

### Envoy Image

The Envoy image is built and pushed to ECR separately:

```bash
# Build and push (content-hash tag, skips if already exists)
./poe build-envoy-push

# Build locally only
./poe build-envoy
```

---

## Terraform Only

The old CDK stack has been removed. Use `infra/terraform/` for deployment and `infra/envoy/` for local Envoy assets and image builds.
