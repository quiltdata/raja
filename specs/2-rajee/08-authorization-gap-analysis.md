# Authorization Gap Analysis - Critical Finding

## The Question: How Will Envoy Do JWT Checks?

**Short Answer:** 🚨 **IT DOESN'T** - There is NO JWT validation in the current implementation.

## Current State: No Authorization

### What AUTH_DISABLED Actually Does

[infra/raja_poc/assets/envoy/entrypoint.sh](infra/raja_poc/assets/envoy/entrypoint.sh#L4-L25):

```bash
AUTH_DISABLED_VALUE="${AUTH_DISABLED:-true}"

if [ "$AUTH_DISABLED_VALUE" = "true" ]; then
  AUTH_FILTER=""  # ← No filter inserted
else
  AUTH_FILTER=$(cat <<'EOF'
                  - name: envoy.filters.http.fault
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.fault.v3.HTTPFault
                      abort:
                        http_status: 403
                        percentage:
                          numerator: 100
  # ← This is NOT JWT validation, it's a fault injection filter!
EOF
)
fi
```

### Current Behavior

**When `AUTH_DISABLED=true` (default):**

```
Request → ALB → Envoy → S3
              │
              └─ NO JWT checks
                 NO authorization
                 OPEN PROXY to S3
```

**When `AUTH_DISABLED=false`:**

```
Request → ALB → Envoy → [FAULT FILTER: 403]
              │
              └─ Always returns 403
                 NOT JWT validation
                 Just blocks everything
```

### What's Missing

**No JWT validation anywhere:**

- ❌ No `envoy.filters.http.jwt_authn` filter
- ❌ No JWT signature verification
- ❌ No token introspection
- ❌ No ext_authz to external service
- ❌ No scope checking

**Current filter chain:**

```yaml
http_filters:
  __AUTH_FILTER__  # ← Either empty or fault injection
  - name: envoy.filters.http.router
```

**What's actually needed:**

```yaml
http_filters:
  - name: envoy.filters.http.jwt_authn  # ← MISSING!
    typed_config:
      providers:
        raja_jwt:
          issuer: "raja-token-service"
          local_jwks:
            inline_string: "..." # Public key for verification
  - name: envoy.filters.http.router
```

## The Authorization Gap

### What Was Removed (Commit 20c1106)

**Old architecture with authorizer sidecar:**

```
┌─────────────────────────────────────┐
│ ECS Task                            │
│                                     │
│  ┌───────────┐    ┌──────────────┐ │
│  │  Envoy    │───▶│ Authorizer   │ │
│  │           │    │  Sidecar     │ │
│  │ ext_authz │    │              │ │
│  │  filter   │    │ - JWT verify │ │
│  │           │    │ - Scope check│ │
│  │           │◀───│ - ALLOW/DENY │ │
│  └───────────┘    └──────────────┘ │
│                          │          │
│                          ▼          │
│                   JWT_SECRET        │
│                   (from ServicesStack) │
└─────────────────────────────────────┘
```

**Authorization flow:**

1. Envoy receives request with JWT in header
2. Envoy calls authorizer sidecar via ext_authz (localhost:9000)
3. Authorizer validates JWT signature using JWT_SECRET
4. Authorizer checks scopes against requested resource/action
5. Authorizer returns ALLOW/DENY to Envoy
6. Envoy proxies request to S3 or returns 403

**File:** [lambda_handlers/authorizer/Dockerfile](lambda_handlers/authorizer/Dockerfile) (removed)

### What Exists Now

**Current architecture:**

```
┌─────────────────────────────────┐
│ ECS Task                        │
│                                 │
│  ┌───────────┐                 │
│  │  Envoy    │                 │
│  │           │                 │
│  │ NO AUTH   │                 │
│  │ (fail-open)                 │
│  └───────────┘                 │
│                                 │
└─────────────────────────────────┘
```

**Authorization flow:**

1. Envoy receives request
2. Envoy proxies directly to S3
3. ❌ No JWT validation
4. ❌ No scope checking
5. ❌ **ANYONE** with network access can use the proxy

### The Gap

**What's missing:**

```
┌─────────────────────────────────────────────┐
│ MISSING: JWT Authorization Layer            │
│                                             │
│ Options:                                    │
│ 1. Envoy JWT filter (inline)                │
│ 2. Envoy ext_authz to ServicesStack API     │
│ 3. Authorizer sidecar (old approach)       │
│ 4. AWS ALB authentication                   │
│                                             │
│ Current: NONE ❌                            │
└─────────────────────────────────────────────┘
```

## Why the Dependency Was Removed

### The Assumption

**Commit message:** "Drop authorizer sidecar from Envoy stack"

**Implied assumption:** Authorization would be handled differently (but not implemented yet)

### What Actually Happened

**Removed:**

- ✅ Authorizer sidecar container
- ✅ JWT_SECRET cross-stack reference
- ✅ Stack dependency
- ✅ ext_authz filter configuration

**Added:**

- ❌ Nothing to replace authorization
- ⚠️ `AUTH_DISABLED` flag (fail-open mode)
- ⚠️ Fault injection filter (not authorization)

### Current Use Case

**Apparent intention:** Bootstrap/testbed environment

**Reality:** Open S3 proxy with no authorization

## Security Implications

### Current Risk Level: 🔴 HIGH

**Anyone can:**

1. Send requests to the ALB endpoint
2. Proxy through Envoy to S3
3. Access S3 objects (limited by IAM task role)
4. No JWT required
5. No scope checking

**Mitigations in place:**

- ALB can be made internal-only (not public)
- IAM task role limits S3 access to specific bucket
- VPC security groups restrict network access

**Still problematic:**

- Any service in the VPC can use the proxy
- No per-user authorization
- No audit trail of who accessed what

### Deployment State

**CloudFormation parameter:** `AUTH_DISABLED=true` (default)

[infra/raja_poc/stacks/rajee_envoy_stack.py:111-118](infra/raja_poc/stacks/rajee_envoy_stack.py#L111-L118):

```python
auth_disabled = CfnParameter(
    self,
    "AUTH_DISABLED",
    type="String",
    default="true",  # ← Fail-open by default
    allowed_values=["true", "false"],
    description="Disable authorization checks in Envoy (fail-open for bootstrap).",
)
```

**Impact:** Stack deploys with authorization **explicitly disabled**.

## Three Possible Solutions

### Option 1: Envoy JWT Filter (Inline Validation)

**Architecture:**

```
Request → ALB → Envoy (JWT filter) → S3
                  │
                  ├─ Validate JWT signature
                  ├─ Check expiration
                  ├─ Extract claims
                  └─ ALLOW/DENY
```

**Envoy configuration:**

```yaml
http_filters:
  - name: envoy.filters.http.jwt_authn
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication
      providers:
        raja_provider:
          issuer: "https://api.example.com"
          audiences:
            - "raja-s3-proxy"
          remote_jwks:
            http_uri:
              uri: "https://api.example.com/.well-known/jwks.json"
              cluster: jwks_cluster
              timeout: 5s
            cache_duration: 600s
      rules:
        - match:
            prefix: "/"
          requires:
            provider_name: raja_provider
  - name: envoy.filters.http.router
```

**Pros:**

- ✅ No external dependencies
- ✅ High performance (local validation)
- ✅ Standard Envoy filter
- ✅ No cross-stack CloudFormation dependency

**Cons:**

- ❌ Only validates JWT signature/expiration
- ❌ Does NOT check RAJA scopes
- ❌ Cannot do custom authorization logic
- ❌ Needs public JWKS endpoint

**Scope checking:** Not possible with JWT filter alone

### Option 2: Envoy ext_authz to ServicesStack API

**Architecture:**

```
Request → ALB → Envoy → S3
              │
              └─ ext_authz HTTP filter
                  │
                  ▼
            ServicesStack API
            POST /authorize
                  │
                  ├─ Validate JWT
                  ├─ Check RAJA scopes
                  ├─ Log to audit table
                  └─ ALLOW/DENY
```

**Envoy configuration:**

```yaml
http_filters:
  - name: envoy.filters.http.ext_authz
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.filters.http.ext_authz.v3.ExtAuthz
      http_service:
        server_uri:
          uri: "https://api.example.com/authorize"
          cluster: authz_cluster
          timeout: 1s
        authorization_request:
          allowed_headers:
            patterns:
              - exact: authorization
              - exact: x-raja-resource
              - exact: x-raja-action
        authorization_response:
          allowed_upstream_headers:
            patterns:
              - exact: x-raja-decision
              - exact: x-raja-reason
  - name: envoy.filters.http.router
```

**Pros:**

- ✅ Full RAJA authorization (scopes, audit, etc.)
- ✅ No CloudFormation cross-stack dependency
- ✅ Reuses existing ServicesStack control plane
- ✅ Can log authorization decisions
- ✅ Can evolve authorization logic without Envoy changes

**Cons:**

- ⚠️ Network latency (HTTP call per request)
- ⚠️ Runtime dependency on ServicesStack API
- ⚠️ Need to handle API unavailability (fail-open vs fail-closed)

**Scope checking:** ✅ Full support

### Option 3: Restore Authorizer Sidecar

**Architecture:**

```
Request → ALB → Envoy → S3
              │
              └─ ext_authz to localhost:9000
                  │
                  ▼
            Authorizer Sidecar
            (in same ECS task)
                  │
                  ├─ Validate JWT
                  ├─ Check RAJA scopes
                  └─ ALLOW/DENY
```

**Pros:**

- ✅ Low latency (localhost)
- ✅ Full RAJA authorization
- ✅ No external network dependency

**Cons:**

- ❌ Requires JWT_SECRET in RajeeEnvoyStack
- ❌ Restores CloudFormation cross-stack dependency
- ❌ Tight coupling between stacks
- ❌ More complex ECS task (multiple containers)
- ❌ Container coordination complexity

**Scope checking:** ✅ Full support

## Recommended Solution

### Phase 1: Envoy JWT Filter (Basic Validation)

**Quick win:** Add JWT signature validation without scope checking

**Use case:** Verify token is valid and not expired

**Limitation:** Cannot enforce RAJA scopes

### Phase 2: Envoy ext_authz to ServicesStack (Full Authorization)

**Target architecture:** Reuse existing control plane

**Benefits:**

- No CloudFormation dependencies
- Full RAJA scope checking
- Audit logging
- Independent stack deployment

**Implementation:**

```python
# infra/raja_poc/app.py
rajee_envoy_stack = RajeeEnvoyStack(
    app,
    "RajeeEnvoyStack",
    authz_endpoint=services_stack.api_url,  # Pass as string parameter
)

# No add_dependency() needed - runtime HTTP call, not CloudFormation import
```

### Phase 3: Optimize with Caching

**Add caching layer:**

- Local token cache in Envoy (reduce API calls)
- Short-lived cache (30-60 seconds)
- Invalidate on 401 responses

## Current Status Summary

### What Works Now

✅ RajeeEnvoyStack deploys independently
✅ Envoy proxies requests to S3
✅ Health checks work
✅ CloudWatch metrics published
✅ Auto-scaling configured

### What's Broken

❌ **NO JWT VALIDATION** - Open proxy
❌ **NO SCOPE CHECKING** - Anyone can access
❌ **NO AUTHORIZATION** - Fail-open mode
❌ **NO AUDIT LOGGING** - No record of access

### What's Needed

1. **Immediate (Bootstrap):** Document that AUTH_DISABLED=true is intentional
2. **Short-term (MVP):** Implement Envoy JWT filter for basic validation
3. **Medium-term (Production):** Implement ext_authz to ServicesStack API
4. **Long-term (Optimization):** Add caching and performance tuning

## Testing Impact

### Current Tests

**Local Docker test:** [infra/test-docker.sh](infra/test-docker.sh)

```bash
# What it tests:
✅ Envoy starts and responds
✅ Health checks pass
✅ S3 proxy works

# What it DOESN'T test:
❌ JWT validation (none exists)
❌ Authorization (disabled)
❌ Scope checking (not implemented)
```

### Needed Tests

**Authorization tests:**

```bash
# Test 1: Reject missing JWT
curl -X GET https://rajee.example.com/bucket/key
# Expected: 401 Unauthorized
# Actual: 200 OK (proxies to S3) ❌

# Test 2: Reject invalid JWT
curl -X GET https://rajee.example.com/bucket/key \
  -H "Authorization: Bearer invalid"
# Expected: 401 Unauthorized
# Actual: 200 OK (proxies to S3) ❌

# Test 3: Reject insufficient scopes
curl -X GET https://rajee.example.com/bucket/key \
  -H "Authorization: Bearer $TOKEN_WITHOUT_READ"
# Expected: 403 Forbidden
# Actual: 200 OK (proxies to S3) ❌
```

## Documentation Updates

### Architecture Diagrams Need Correction

**Current docs show:** Authorization via sidecar or API

**Actual implementation:** No authorization

**Action needed:** Update diagrams to show:

1. Current state (fail-open, no auth)
2. Planned state (ext_authz to API)
3. Migration path

### README Needs Warning

**Add prominent notice:**

```markdown
## ⚠️ Security Warning

RajeeEnvoyStack currently deploys with `AUTH_DISABLED=true` by default.

**This means:**
- No JWT validation
- No authorization checks
- Open S3 proxy

**Only deploy in:**
- Development environments
- Isolated VPCs
- Internal-only networks

**Production deployment requires:**
- Implementing ext_authz filter (see specs/2-rajee/08-authorization-gap-analysis.md)
- Configuring JWT validation
- Setting AUTH_DISABLED=false
```

## Answers to Original Question

### "How will Envoy do JWT checks?"

**Current answer:** It doesn't.

**Future answer (Option 2 - Recommended):**

```
1. Request arrives at Envoy with JWT in Authorization header
2. Envoy ext_authz filter extracts JWT + resource info
3. Envoy makes HTTP POST to ServicesStack:
   POST /authorize
   {
     "token": "eyJ...",
     "resource": "Bucket::my-bucket",
     "action": "read",
     "key": "/my-key"
   }
4. ServicesStack validates JWT and checks scopes
5. ServicesStack returns ALLOW/DENY
6. Envoy proxies to S3 or returns 403
```

**No CloudFormation dependency needed** - Just runtime HTTP calls

### "Will removing the dependency work?"

**Infrastructure:** Yes ✅ (stacks deploy independently)

**Authorization:** No ❌ (no JWT validation exists)

**Resolution:** Implement ext_authz HTTP filter to ServicesStack API

## Next Steps

### Priority 1: Document Current State

✅ This document explains the gap

### Priority 2: Add Security Warning

📋 Update README with security notice

### Priority 3: Implement Authorization

📋 Add ext_authz filter calling ServicesStack API

**Rough timeline:**

- Document: Done ✅
- Security warning: 30 minutes
- ext_authz implementation: 4-8 hours
- Testing: 2-4 hours

## References

- Commit: `20c1106` - "Drop authorizer sidecar from Envoy stack"
- Current Envoy config: [infra/raja_poc/assets/envoy/envoy.yaml.tmpl](infra/raja_poc/assets/envoy/envoy.yaml.tmpl)
- Entrypoint: [infra/raja_poc/assets/envoy/entrypoint.sh](infra/raja_poc/assets/envoy/entrypoint.sh)
- Stack definition: [infra/raja_poc/stacks/rajee_envoy_stack.py](infra/raja_poc/stacks/rajee_envoy_stack.py)
- Envoy JWT filter docs: <https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/jwt_authn_filter>
- Envoy ext_authz docs: <https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_authz_filter>
