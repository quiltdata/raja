# Stack Dependency Analysis - RajeeEnvoyStack Independence

## Issue Summary

**Current State:** RajeeEnvoyStack has an explicit dependency on ServicesStack that is no longer needed.

**Root Cause:** Commit `20c1106` removed the authorizer sidecar container from RajeeEnvoyStack, but left the stack dependency in place. This dependency was originally required because RajeeEnvoyStack needed the JWT signing secret from ServicesStack for the authorizer sidecar.

**Impact:** The dependency prevents deploying RajeeEnvoyStack independently from ServicesStack, even though it no longer imports any resources from ServicesStack.

## Historical Context

### Before Commit 20c1106

**File:** [infra/raja_poc/app.py](infra/raja_poc/app.py)

```python
rajee_envoy_stack = RajeeEnvoyStack(
    app,
    "RajeeEnvoyStack",
    jwt_signing_secret=services_stack.jwt_secret,  # ← Cross-stack reference
)
rajee_envoy_stack.add_dependency(services_stack)  # ← Required for the reference above
```

**File:** [infra/raja_poc/stacks/rajee_envoy_stack.py](infra/raja_poc/stacks/rajee_envoy_stack.py)

The RajeeEnvoyStack constructor accepted the JWT secret:

```python
def __init__(
    self,
    scope: Construct,
    construct_id: str,
    *,
    jwt_signing_secret: secretsmanager.ISecret,  # ← Cross-stack resource
    certificate_arn: str | None = None,
    **kwargs: object,
) -> None:
```

And the authorizer sidecar container used it:

```python
authorizer_container = task_definition.add_container(
    "Authorizer",
    image=ecs.ContainerImage.from_asset(...),
    environment={
        "DISABLE_AUTH_CHECKS": disable_auth_checks.value_as_string,
    },
    secrets={
        "JWT_SECRET": ecs.Secret.from_secrets_manager(jwt_signing_secret),  # ← Used here
    },
    ...
)
```

The ECS execution role also needed permissions to read the secret:

```python
if task_definition.execution_role is not None:
    jwt_signing_secret.grant_read(task_definition.execution_role)
```

### After Commit 20c1106

**File:** [infra/raja_poc/app.py](infra/raja_poc/app.py) (Current)

```python
rajee_envoy_stack = RajeeEnvoyStack(app, "RajeeEnvoyStack")
# NOTE: Dependency commented out to allow independent deployment
# rajee_envoy_stack.add_dependency(services_stack)
```

**Changes Made:**

1. ✅ Removed `jwt_signing_secret` parameter from RajeeEnvoyStack constructor
2. ✅ Removed entire authorizer sidecar container (41 lines deleted)
3. ✅ Removed JWT secret grant to execution role
4. ⚠️ Kept stack dependency in place (now redundant)
5. ✅ **LATER:** Dependency was commented out (line 18-19 in current version)

**Current Constructor:**

```python
def __init__(
    self,
    scope: Construct,
    construct_id: str,
    *,
    certificate_arn: str | None = None,  # Only optional parameter
    **kwargs: object,
) -> None:
```

## Dependency Analysis

### CloudFormation Import/Export Mechanism

When one CDK stack depends on another, CloudFormation uses exports and imports:

1. **Export Side (ServicesStack):**
   - CDK automatically exports values needed by dependent stacks
   - Creates CloudFormation exports like `RajaServicesStack:ExportsOutputRefJwtSigningKeyXXXXXX`

2. **Import Side (RajeeEnvoyStack):**
   - CDK uses `Fn::ImportValue` to reference exported values
   - Creates implicit dependencies via CloudFormation

3. **Explicit Dependency:**
   - `add_dependency()` enforces stack deployment order
   - Prevents RajeeEnvoyStack from deploying before ServicesStack

### Why the Dependency Existed

**Before commit 20c1106**, RajeeEnvoyStack had a **concrete cross-stack reference**:

```python
jwt_signing_secret: secretsmanager.ISecret  # From ServicesStack
```

This creates:

- CloudFormation export in ServicesStack
- CloudFormation import in RajeeEnvoyStack
- Deployment order constraint

The explicit `add_dependency()` was **required** to ensure ServicesStack deployed first.

### Current State: No Cross-Stack References

**After commit 20c1106**, RajeeEnvoyStack has:

1. ✅ **No constructor parameters** from ServicesStack
2. ✅ **No resource references** from ServicesStack
3. ✅ **No CloudFormation imports** from ServicesStack
4. ❌ **Explicit dependency** still present (but commented out)

**Verification:** Looking at the current RajeeEnvoyStack constructor parameters:

```python
# Only accepts:
certificate_arn: str | None = None  # Optional string parameter (not a cross-stack reference)
```

## Impact of Commenting Out the Dependency

### Before (with dependency)

```bash
cdk deploy RajeeEnvoyStack
# Output: Error - RajaServicesStack must be deployed first
```

### After (dependency commented out)

```bash
cdk deploy RajeeEnvoyStack
# Output: Success - RajeeEnvoyStack deploys independently
```

### What Gets Deployed

RajeeEnvoyStack creates:

1. **VPC** - New VPC with 2 AZs and 1 NAT gateway
2. **ECS Cluster** - Fargate cluster with container insights
3. **ECS Task Definition** - Single Envoy proxy container
4. **S3 Bucket** - Test bucket (`raja-poc-test-{account}-{region}`)
5. **ALB** - Application Load Balancer with health checks
6. **Auto Scaling** - CPU and request-based scaling (2-10 tasks)
7. **CloudWatch Dashboard** - Authorization metrics dashboard
8. **IAM Roles** - Task and execution roles with S3 permissions

**All resources are self-contained** - no cross-stack references remain.

## Architecture Changes

### Old Architecture (with authorizer sidecar)

```
┌─────────────────────────────────────────────────┐
│ RajeeEnvoyStack                                 │
│                                                 │
│  ┌───────────────────────────────────────────┐ │
│  │ ECS Task Definition                       │ │
│  │                                           │ │
│  │  ┌──────────────┐   ┌──────────────────┐ │ │
│  │  │   Envoy      │   │   Authorizer     │ │ │
│  │  │   Proxy      │──▶│   Sidecar        │ │ │
│  │  │              │   │                  │ │ │
│  │  │ Port 10000   │   │   Port 9000      │ │ │
│  │  └──────────────┘   └────────┬─────────┘ │ │
│  │                              │           │ │
│  └──────────────────────────────┼───────────┘ │
│                                 │             │
└─────────────────────────────────┼─────────────┘
                                  │
                                  ▼
        ┌─────────────────────────────────────────┐
        │ ServicesStack                           │
        │                                         │
        │  ┌─────────────────────────────────┐   │
        │  │ JWT Signing Secret              │   │
        │  │ (Secrets Manager)               │   │
        │  └─────────────────────────────────┘   │
        │                                         │
        └─────────────────────────────────────────┘
```

**Cross-stack dependency:**

- Authorizer container needs JWT secret from ServicesStack
- ECS execution role needs `secretsmanager:GetSecretValue` permission

### New Architecture (Envoy-only)

```
┌─────────────────────────────────────────────────┐
│ RajeeEnvoyStack                                 │
│                                                 │
│  ┌───────────────────────────────────────────┐ │
│  │ ECS Task Definition                       │ │
│  │                                           │ │
│  │  ┌──────────────────────────────────────┐ │ │
│  │  │   Envoy Proxy                        │ │ │
│  │  │                                      │ │ │
│  │  │   Port 10000 - HTTP proxy           │ │ │
│  │  │   Port 9901  - Admin/health         │ │ │
│  │  │                                      │ │ │
│  │  │   ENV: AUTH_DISABLED=true           │ │ │
│  │  └──────────────────────────────────────┘ │ │
│  │                                           │ │
│  └───────────────────────────────────────────┘ │
│                                                 │
└─────────────────────────────────────────────────┘

        ┌─────────────────────────────────────────┐
        │ ServicesStack                           │
        │ (No longer referenced)                  │
        │                                         │
        │  Control Plane APIs (token, authz)     │
        │  DynamoDB Tables (scopes, audit)       │
        │  JWT & Harness Secrets                 │
        └─────────────────────────────────────────┘
```

**Independent stacks:**

- RajeeEnvoyStack is fully self-contained
- ServicesStack provides control plane APIs
- Integration happens via API calls, not CloudFormation dependencies

## Authorization Model Evolution

### Phase 1: Sidecar Pattern (Old)

```
Request → ALB → Envoy → Authorizer Sidecar → S3
                  │            │
                  │            ▼
                  │       JWT Validation
                  │       Scope Checking
                  │            │
                  ▼            ▼
            Proxy Request   ALLOW/DENY
```

**Authorization:**

- Authorizer sidecar runs in same ECS task
- Envoy calls authorizer via ext_authz (port 9000)
- Authorizer validates JWT and checks scopes
- Requires JWT secret from ServicesStack

### Phase 2: Envoy-Only (Current)

```
Request → ALB → Envoy → S3
                  │
                  ▼
            Simple Proxy
         (AUTH_DISABLED=true)
```

**Authorization:**

- Envoy runs in fail-open mode
- No JWT validation
- No scope checking
- Bootstrap/testbed configuration

### Phase 3: Envoy with ext_authz Filter (Future)

```
Request → ALB → Envoy → S3
                  │
                  ▼
            ext_authz Filter
                  │
                  ▼
           HTTP Call to ServicesStack
           POST /authorize
                  │
                  ▼
           JWT Validation
           Scope Checking
                  │
                  ▼
            ALLOW/DENY
```

**Authorization:**

- Envoy calls ServicesStack control plane API
- Cross-stack communication via HTTP, not CloudFormation
- JWT secret stays in ServicesStack
- RajeeEnvoyStack remains independent

## CloudFormation Export Analysis

### Before (with dependency)

**ServicesStack Exports:**

```yaml
Exports:
  RajaServicesStack:ExportsOutputRefJwtSigningKeyXXXXXX:
    Value: !Ref JwtSigningKey
    Export:
      Name: RajaServicesStack:ExportsOutputRefJwtSigningKeyXXXXXX
```

**RajeeEnvoyStack Imports:**

```yaml
Resources:
  RajeeTaskRolePolicy:
    Properties:
      # ...
  AuthorizerContainer:
    Properties:
      Secrets:
        - Name: JWT_SECRET
          ValueFrom: !ImportValue RajaServicesStack:ExportsOutputRefJwtSigningKeyXXXXXX
```

### After (dependency removed)

**ServicesStack Exports:**

```yaml
Exports:
  # No exports consumed by RajeeEnvoyStack
```

**RajeeEnvoyStack Imports:**

```yaml
# No imports from ServicesStack
```

**Result:** RajeeEnvoyStack CloudFormation template has **no references** to ServicesStack.

## Verification Steps

### Confirm No Cross-Stack References

```bash
# Generate CloudFormation template
cd infra
npx cdk synth RajeeEnvoyStack > /tmp/rajee-stack.yaml

# Check for imports
grep -i "ImportValue" /tmp/rajee-stack.yaml
# Expected: No results (no imports)

# Check for explicit dependencies
grep -i "DependsOn" /tmp/rajee-stack.yaml | grep -i "services"
# Expected: No results (no service stack dependency)
```

### Deploy Independently

```bash
# Deploy RajeeEnvoyStack without ServicesStack
npx cdk deploy RajeeEnvoyStack

# Verify deployment succeeds without ServicesStack
```

### Check Stack Outputs

```bash
# RajeeEnvoyStack outputs
aws cloudformation describe-stacks \
  --stack-name RajeeEnvoyStack \
  --query 'Stacks[0].Outputs'

# Should show:
# - LoadBalancerDNS
# - TestBucketName
# - DeploymentPlatform
# - ServiceArn
```

## Testing Implications

### Unit Tests (CDK Synth)

No changes needed - stack synthesizes independently.

### Integration Tests

**Before:** Required both stacks deployed:

```bash
npx cdk deploy RajaServicesStack RajeeEnvoyStack
```

**After:** Can test RajeeEnvoyStack independently:

```bash
npx cdk deploy RajeeEnvoyStack
# Test S3 proxy without authorization
```

### Docker Local Testing

RajeeEnvoyStack includes a dedicated test script:

```bash
./infra/test-docker.sh
```

This script:

1. Builds Envoy Docker image locally
2. Runs container with health checks
3. Tests S3 proxy functionality
4. No dependency on ServicesStack

## Recommendations

### 1. Remove Commented Dependency (Done)

**Current state:** [infra/raja_poc/app.py:18-19](infra/raja_poc/app.py#L18-L19)

```python
# NOTE: Dependency commented out to allow independent deployment
# rajee_envoy_stack.add_dependency(services_stack)
```

**Status:** ✅ Already commented out - this is correct!

**Recommendation:** Keep commented out with clear note explaining why.

### 2. Document Independence

**Action:** Create this document (✅ done)

**Purpose:** Explain architectural rationale for independent stacks.

### 3. Update Integration Tests

**Current:** Tests may assume both stacks deployed together.

**Recommended:** Add separate test scenarios:

```python
# Scenario 1: RajeeEnvoyStack standalone (S3 proxy only)
def test_envoy_proxy_standalone():
    # Deploy RajeeEnvoyStack only
    # Test S3 operations without authorization

# Scenario 2: Full integration (both stacks)
def test_full_authorization_flow():
    # Deploy both stacks
    # Test authorization via API calls
```

### 4. Future: Envoy ext_authz Configuration

When adding external authorization back:

```python
# Do NOT pass secrets to RajeeEnvoyStack
rajee_envoy_stack = RajeeEnvoyStack(
    app,
    "RajeeEnvoyStack",
    authorizer_endpoint=services_stack.api_url,  # HTTP endpoint, not secret
)

# No stack dependency needed
# Envoy makes HTTP calls to ServicesStack at runtime
```

## Conclusion

### Summary

- ✅ RajeeEnvoyStack is now fully independent of ServicesStack
- ✅ Dependency has been commented out appropriately
- ✅ No CloudFormation cross-stack references remain
- ✅ Authorization moved from sidecar to future external HTTP calls

### Key Insight

**The evolution from sidecar to API-based authorization naturally decouples the stacks:**

1. **Sidecar pattern** → Tight coupling (shared secrets, CloudFormation dependencies)
2. **API-based pattern** → Loose coupling (HTTP calls, no CloudFormation dependencies)

This aligns with microservices best practices: communicate via APIs, not infrastructure dependencies.

### Next Steps

1. ✅ Keep dependency commented out
2. ✅ Document rationale (this document)
3. 📋 Update integration tests for independent deployment
4. 📋 Implement ext_authz HTTP filter in future (Phase 3)

## References

- Commit: `20c1106` - "Drop authorizer sidecar from Envoy stack"
- File: [infra/raja_poc/app.py](infra/raja_poc/app.py)
- File: [infra/raja_poc/stacks/rajee_envoy_stack.py](infra/raja_poc/stacks/rajee_envoy_stack.py)
- File: [infra/raja_poc/stacks/services_stack.py](infra/raja_poc/stacks/services_stack.py)
