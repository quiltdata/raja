# 14. Deployment Optimization Proposal

**Date**: 2026-01-16
**Status**: PROPOSED
**Context**: Addressing slow ECS deployments even when no code changes occur

## Problem Statement

Current CDK deployments of the Rajee Envoy stack take 3-5 minutes even when no application code has changed. This significantly slows the development feedback loop and increases CI/CD pipeline duration.

### Current Behavior

From deployment logs:
```
RajeeEnvoyStack: success: Published RajeeTask/EnvoyProxy/AssetImage
[████████▎·················································] (1/7)
3:49:24 PM | UPDATE_IN_PROGRESS | AWS::CloudFormation::Stack | RajeeEnvoyStack
3:49:35 PM | UPDATE_IN_PROGRESS | AWS::ECS::Service | RajeeService/Service/Service
```

The deployment process takes 3-5 minutes due to:
1. Docker image rebuild on every `cdk deploy`
2. ECS rolling deployment with health check validation
3. ALB target group registration and draining

## Root Cause Analysis

### 1. Docker Asset Rebuilds

**Location**: `infra/raja_poc/stacks/rajee_envoy_stack.py:146-151`

```python
image=ecs.ContainerImage.from_asset(
    str(repo_root),
    file="infra/raja_poc/assets/envoy/Dockerfile",
    exclude=asset_excludes,
    platform=docker_platform,
)
```

**Issue**: CDK's `from_asset()` computes a hash of the entire repository directory. Any file change (including CDK outputs, Python cache files, or git metadata) triggers:
- Docker image rebuild
- ECR push with new tag
- ECS task definition update
- ECS service redeployment with full health checks

### 2. Conservative Health Check Timing

**Container Health Check** (`rajee_envoy_stack.py:163-169`):
- Interval: 30 seconds
- Start period: 60 seconds
- Retries: 3
- **Time to healthy**: 60-90 seconds

**ALB Target Group Health Check** (`rajee_envoy_stack.py:209-216`):
- Interval: 30 seconds
- Healthy threshold: 2 consecutive successes
- Unhealthy threshold: 3 consecutive failures
- **Time to healthy**: ~60 seconds

### 3. Rolling Deployment Strategy

**Configuration** (`rajee_envoy_stack.py:194-198`):
- Desired count: 2 tasks
- Min healthy percent: 50%
- Max healthy percent: 200%
- Health check grace period: 30 seconds

**Process**:
1. Start 2 new tasks (200% = 4 total) - ~30s
2. Wait for container health checks - ~60-90s
3. Wait for ALB registration - ~60s
4. Drain old task connections - ~30s
5. Stop old tasks - ~10s

**Total**: 3-5 minutes minimum

## Proposed Solutions

### Solution 1: ECR Image Registry with Content-Based Tags (RECOMMENDED)

Decouple image building from CDK deployment. Build and push images separately with immutable, content-based tags.

#### Implementation

**Step 1**: Add ECR repository to infrastructure

```python
# In rajee_envoy_stack.py
from aws_cdk import aws_ecr as ecr

# Create ECR repository with lifecycle policy
envoy_repo = ecr.Repository(
    self,
    "EnvoyRepository",
    repository_name="raja/envoy",
    removal_policy=RemovalPolicy.RETAIN,
    lifecycle_rules=[
        ecr.LifecycleRule(
            description="Keep last 10 images",
            max_image_count=10,
        )
    ],
)
```

**Step 2**: Add image build and push script

```bash
#!/bin/bash
# scripts/build-envoy-image.sh

set -e

# Get git commit hash for tagging
GIT_HASH=$(git rev-parse --short HEAD)
IMAGE_TAG="${GIT_HASH}"

# Get ECR repository URI from CDK outputs
REPO_URI=$(aws cloudformation describe-stacks \
    --stack-name RajeeEnvoyStack \
    --query 'Stacks[0].Outputs[?OutputKey==`EnvoyRepositoryUri`].OutputValue' \
    --output text)

# Build image
echo "Building image with tag: ${IMAGE_TAG}"
docker build \
    -f infra/raja_poc/assets/envoy/Dockerfile \
    -t "raja-envoy:${IMAGE_TAG}" \
    .

# Tag for ECR
docker tag "raja-envoy:${IMAGE_TAG}" "${REPO_URI}:${IMAGE_TAG}"

# Login to ECR
aws ecr get-login-password --region us-east-1 | \
    docker login --username AWS --password-stdin ${REPO_URI}

# Push to ECR
docker push "${REPO_URI}:${IMAGE_TAG}"

echo "Image pushed: ${REPO_URI}:${IMAGE_TAG}"
echo "Export IMAGE_TAG=${IMAGE_TAG} before deploying"
```

**Step 3**: Update CDK to use ECR image

```python
# In rajee_envoy_stack.py
import os

# Get image tag from environment (defaults to 'latest' for development)
image_tag = os.getenv("IMAGE_TAG", "latest")

envoy_container = task_definition.add_container(
    "EnvoyProxy",
    image=ecs.ContainerImage.from_ecr_repository(
        repository=envoy_repo,
        tag=image_tag,
    ),
    # ... rest of configuration
)
```

**Step 4**: Update deployment workflow

```bash
# Development: Build and deploy with latest
./scripts/build-envoy-image.sh
export IMAGE_TAG=$(git rev-parse --short HEAD)
./poe deploy

# CI/CD: Explicit image tag
./scripts/build-envoy-image.sh
export IMAGE_TAG=$GITHUB_SHA
cdk deploy --require-approval never
```

#### Benefits

- **No-op deployments**: If image hasn't changed, no ECS update triggered
- **Immutable tags**: Content-based tags prevent accidental overwrites
- **Build caching**: Docker layer caching works across builds
- **Faster CI/CD**: Image building can be cached or skipped if unchanged
- **Deployment traceability**: Image tags map to git commits

#### Tradeoffs

- **More complex workflow**: Requires separate build step
- **Manual coordination**: Developers must remember to build image
- **ECR costs**: Storing images in ECR (minimal cost)

### Solution 2: Optimized Asset Excludes (QUICK FIX)

Improve the `asset_excludes` list to prevent spurious rebuilds.

```python
# In rajee_envoy_stack.py
asset_excludes = [
    # Version control
    ".git",
    ".gitignore",

    # Python
    ".venv",
    "**/*.pyc",
    "**/__pycache__",
    "**/*.egg-info",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "*.coverage",
    ".coverage",
    "htmlcov",

    # Node/CDK
    "node_modules",
    "cdk.out",
    "cdk.context.json",

    # IDE
    ".vscode",
    ".idea",
    "*.swp",
    "*.swo",

    # Documentation/specs
    "specs",
    "docs",
    "*.md",

    # Tests (if not needed in container)
    "tests",

    # CI/CD
    ".github",
    ".gitlab-ci.yml",

    # Other
    "tmp",
    ".DS_Store",
]
```

#### Benefits

- **Quick implementation**: Single code change
- **No workflow changes**: Existing deployment process unchanged
- **Reduces false rebuilds**: Fewer spurious file changes trigger rebuilds

#### Tradeoffs

- **Still rebuilds on code changes**: Any Python file change triggers rebuild
- **Hash computation overhead**: CDK still computes hash of remaining files
- **Not foolproof**: Some changes still trigger unnecessary rebuilds

### Solution 3: Development-Specific Optimizations

For non-production environments, accept tradeoffs for faster feedback.

#### Single Task Deployment

```python
# Add context variable for environment
environment = self.node.try_get_context("environment") or "dev"
is_production = environment == "prod"

alb_kwargs = {
    "cluster": cluster,
    "task_definition": task_definition,
    "desired_count": 2 if is_production else 1,
    # ...
    "min_healthy_percent": 100 if is_production else 0,
    "max_healthy_percent": 200,
}

scaling = alb_service.service.auto_scale_task_count(
    min_capacity=2 if is_production else 1,
    max_capacity=10,
)
```

**Deploy**: `cdk deploy -c environment=dev`

#### Faster Health Checks (Development Only)

```python
if not is_production:
    health_check_interval = Duration.seconds(10)
    health_check_start_period = Duration.seconds(30)
else:
    health_check_interval = Duration.seconds(30)
    health_check_start_period = Duration.seconds(60)

envoy_container = task_definition.add_container(
    "EnvoyProxy",
    # ...
    health_check=ecs.HealthCheck(
        command=["CMD-SHELL", "curl -f http://localhost:9901/ready || exit 1"],
        interval=health_check_interval,
        timeout=Duration.seconds(5),
        retries=3,
        start_period=health_check_start_period,
    ),
)
```

#### Benefits

- **Fastest development cycle**: Single task = faster deployments
- **Production safety**: Full health checks in production
- **Simple toggle**: Context variable switches behavior

#### Tradeoffs

- **Environment differences**: Dev doesn't match production exactly
- **Less resilient**: Single task = no redundancy during deployment
- **False confidence**: Fast health checks may miss issues

## Recommendation

Implement **Solution 1 (ECR with content-based tags)** as the primary optimization, with **Solution 2 (better excludes)** as a complementary quick win.

### Implementation Plan

1. **Phase 1: Quick Win** (1 hour)
   - Improve asset excludes list
   - Deploy and verify reduced rebuilds
   - Document remaining rebuild triggers

2. **Phase 2: ECR Migration** (4-6 hours)
   - Add ECR repository to CDK stack
   - Create build script with git-hash tagging
   - Update task definition to use ECR image
   - Add poe task: `./poe build-envoy`
   - Update CI/CD workflow
   - Update documentation

3. **Phase 3: Validation** (1-2 hours)
   - Test no-op deployment (should skip ECS update)
   - Test actual code change deployment
   - Measure deployment time improvements
   - Document new workflow

### Success Metrics

**Before Optimization**:
- No-op deployment: 3-5 minutes
- Code change deployment: 3-5 minutes
- Image rebuilds: Every deployment

**After Optimization**:
- No-op deployment: 0-30 seconds (no ECS update)
- Code change deployment: 2-3 minutes (build + deploy)
- Image rebuilds: Only when Dockerfile or code changes

### Alternative: Local Development with Docker Compose

For even faster iteration, consider local development environment:

```yaml
# docker-compose.yml
services:
  envoy:
    build:
      context: .
      dockerfile: infra/raja_poc/assets/envoy/Dockerfile
    ports:
      - "10000:10000"
      - "9901:9901"
    environment:
      - AUTH_DISABLED=true
      - ENVOY_LOG_LEVEL=debug
    volumes:
      - ./infra/raja_poc/assets/envoy/envoy.yaml:/etc/envoy/envoy.yaml
```

**Development Workflow**:
```bash
# Local testing (instant feedback)
docker-compose up --build

# Deploy to AWS (only when ready)
./scripts/build-envoy-image.sh
export IMAGE_TAG=$(git rev-parse --short HEAD)
./poe deploy
```

## Open Questions

1. **Image lifecycle**: How many images should we retain in ECR? (Proposal: 10)
2. **Tag strategy**: Should we also tag with semantic versions? (e.g., `v1.2.3`)
3. **CI/CD caching**: Should GitHub Actions cache Docker layers?
4. **Multi-stage builds**: Should we optimize the Dockerfile with multi-stage builds?

## References

- [CDK ECS Patterns - Load Balanced Fargate Service](https://docs.aws.amazon.com/cdk/api/v2/docs/aws-cdk-lib.aws_ecs_patterns.ApplicationLoadBalancedFargateService.html)
- [ECS Deployment Configuration](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/deployment-type-ecs.html)
- [ECR Lifecycle Policies](https://docs.aws.amazon.com/AmazonECR/latest/userguide/LifecyclePolicies.html)
- Current implementation: `infra/raja_poc/stacks/rajee_envoy_stack.py`

## Decision

**Status**: Awaiting approval

Please review and approve one of:
- [ ] Solution 1: ECR with content-based tags (recommended)
- [ ] Solution 2: Optimized asset excludes only
- [ ] Solution 3: Development-specific optimizations
- [ ] Combination of solutions (specify)
- [ ] Alternative approach (describe)
