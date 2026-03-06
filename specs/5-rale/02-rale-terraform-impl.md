# RALE: RAJ-Authorizing Logical Endpoint — Terraform Implementation Spec

## Executive Summary

RALE virtualizes S3 access using **manifest-based logical addressing** (Uniform Storage Locators). Instead of exposing physical bucket locations, clients reference logical paths like `registry/my/package@hash/data.csv`. Authorization happens once via compiled TAJs (Translated Access JWTs), with no policy lookups on subsequent requests.

**Critical Context:** RAJEE (the current implementation) does prefix-based authorization entirely in Envoy Lua. RALE requires a **fundamentally different architecture**: two Lambdas (Authorizer + Router) with manifest-based translation. This spec defines what needs to be built.

## Core Hypothesis

> "Location transparency + compiled authorization = secure, performant, infrastructure-agnostic data access."

Traditional S3 approaches (bucket policies, presigned URLs, IAM roles) either expose infrastructure or evaluate authorization on every request. RALE compiles authorization once and caches immutable manifest translations indefinitely.

## Current State: RAJEE vs RALE

### What Exists Today (RAJEE)

**Architecture:**

```text
Client → Envoy Proxy → S3
           ↓
    Lua Authorization Filter
    (validates JWT, checks prefix scopes)
```

**Components:**

1. **Control Plane Lambda** ([lambda_handlers/control_plane/handler.py](rlocation:///Users/ernest/GitHub/raja/lambda_handlers/control_plane/handler.py))
   - FastAPI app via API Gateway
   - Issues JWTs with scopes like `S3Object:bucket/key:s3:GetObject`
   - Uses Secrets Manager for JWT signing keys

2. **Envoy Proxy** ([infra/raja_poc/assets/envoy/authorize.lua](rlocation:///Users/ernest/GitHub/raja/infra/raja_poc/assets/envoy/authorize.lua))
   - Lua filter does ALL authorization
   - Parses S3 requests (method + path → scope)
   - Validates JWT and checks prefix matching
   - Forwards directly to S3 on success

3. **Utilities** (not deployed):
   - `package_resolver` - manifest resolution functions (library only)
   - `authorizer` - health check stub (not actually used)

**Key Limitation:** No logical addressing, no manifest translation, authorization locked in Envoy Lua.

### What RALE Requires (From Article)

**Architecture:**

```text
Client Request (USL: registry/pkg@hash/file.csv)
    ↓
┌─────────────────────────────────────┐
│     Envoy Router/Filter             │
│  (Route based on TAJ presence)      │
└────────────┬────────────────────────┘
             │
        ┌────┴────┐
        │ TAJ?    │
        └────┬────┘
             │
    ┌────────┴────────┐
    NO                YES
    │                 │
    ▼                 ▼
┌─────────────────┐ ┌─────────────────┐
│   Authorizer    │ │     Router      │
│    Lambda       │ │     Lambda      │
│  (NEW)          │ │  (NEW)          │
└─────────────────┘ └─────────────────┘
│                 │
│ - Validate ID   │ - Validate TAJ
│ - Call AVP      │ - Fetch manifest
│ - Mint TAJ      │ - Prove membership
│ - Cache result  │ - Resolve physical URI
│                 │ - SigV4 sign
│                 │ - Forward to S3
└─────────────────┘ └─────────────────┘
```

**Key Differences:**

1. **Authorization moves to Lambda**: Not in Envoy Lua anymore
2. **Manifest translation**: Router Lambda translates logical → physical
3. **Two-stage flow**: Authorizer mints TAJs, Router uses them
4. **Logical addressing**: Clients use USLs, never see physical buckets

## Key Design Decisions

### 1. Manifest Pinning

**Decision:** Each TAJ references a specific immutable manifest hash.

**Rationale:**

- Prevents floating semantics as packages evolve
- Old TAJs intentionally use old manifests
- No time-of-check-time-of-use (TOCTOU) issues
- Immutability enables indefinite caching

**Implication:** TAJ format includes manifest hash; Router Lambda fetches manifest by hash.

### 2. Compiled Authorization

**Decision:** Authorization happens once at TAJ issuance time. No policy lookups on Router path.

**Rationale:**

- Performance: Single AVP call per package per user
- Predictability: Decision frozen at TAJ mint time
- Scalability: Router Lambda only validates signatures
- Transparency: Authorization decision explicit in TAJ

**Implication:** Authorizer Lambda is only entry point to AVP. Router never calls AVP.

### 3. Location Transparency

**Decision:** Clients never interact with physical bucket/key coordinates.

**Rationale:**

- Decouples logical namespace from storage layout
- Enables infrastructure changes without client breakage
- Supports multi-bucket, multi-region datasets
- Facilitates migration and optimization

**Implication:** All client requests use USLs. Router Lambda handles translation.

### 4. Immutable Manifests Enable Infinite Caching

**Decision:** Manifest fetch results cached indefinitely (no TTL).

**Rationale:**

- Content-addressed storage (hash) guarantees immutability
- No stale cache risk
- Reduces manifest fetch overhead to zero after first access
- Supports high-throughput scenarios

**Implication:** Router Lambda uses persistent cache (DynamoDB).

### 5. S3 API Compatibility

**Decision:** RALE exposes standard S3 API surface (GetObject, PutObject, etc.).

**Rationale:**

- Works with existing S3 clients (boto3, AWS CLI, SDKs)
- No client-side library required
- Minimal behavior changes
- Easier adoption

**Implication:** Router must preserve S3 semantics (headers, error codes, multipart).

## Missing Components for RALE

Based on the current RAJEE implementation, here's what needs to be built:

### 1. Authorizer Lambda (NEW)

**Does NOT exist.** Current `lambda_handlers/authorizer/app.py` is just a health check stub.

**Required Functionality:**

- Extract user identity from request
- Parse package from USL (e.g., `registry/pkg@hash`)
- Pin package to specific manifest hash
- Call AVP with `(principal, package, action)`
- Mint TAJ with manifest hash on ALLOW
- Cache decision in DynamoDB (`user_id#manifest_hash` → TAJ, 5min TTL)

### 2. Router Lambda (NEW)

**Does NOT exist.** Current `package_resolver` is just library functions, not deployed.

**Required Functionality:**

- Validate TAJ signature and expiration
- Extract manifest hash from TAJ
- Fetch manifest from cache or resolve via `resolve_package_manifest()`
- Parse logical key from USL path
- Prove membership: `logical_key in manifest`
- Resolve physical S3 URI (bucket, key, version ID)
- Rewrite request to physical S3
- Sign with SigV4 and forward

### 3. Envoy Routing Logic (MODIFY EXISTING)

**Current:** Envoy Lua does authorization directly.

**Required:** Route to Lambda based on TAJ presence:

- If TAJ present and valid → Forward to Router Lambda
- If TAJ missing/invalid → Forward to Authorizer Lambda
- On Lambda error → Fail closed (503)

**Options:**

- **Option A:** Envoy External Processor (gRPC to Lambda)
- **Option B:** Lua script routes to Lambda URLs via HTTP
- **Option C:** API Gateway in front of Envoy (cleanest separation)

### 4. Manifest Cache Table (NEW)

DynamoDB table for immutable manifest caching:

- PK: `manifest_hash`
- Data: JSON blob with logical→physical mappings
- No TTL (content-addressed, immutable)

### 5. TAJ Cache Table (NEW)

DynamoDB table for Authorizer decision caching:

- PK: `user_id#manifest_hash`
- Data: TAJ string, decision (ALLOW/DENY)
- TTL: 5 minutes

### 6. TAJ Token Format (MODIFY EXISTING)

Current tokens have `scopes`. RALE TAJs need:

```json
{
  "sub": "User::alice",
  "iss": "https://api.raja.example.com",
  "exp": 1738901234,
  "grants": ["s3:GetObject/registry/my-package@abc123/"],
  "manifest_hash": "abc123def456",
  "package_name": "my/package",
  "registry": "s3://quilt-registry"
}
```

## Implementation Tasks

### Task 1: Define TAJ Structure

**What:** Extend JWT claims to support manifest-pinned authorization.

**Files to Modify:**

- [src/raja/token.py](rlocation:///Users/ernest/GitHub/raja/src/raja/token.py) - Add TAJ model with manifest fields
- [lambda_handlers/control_plane/handler.py](rlocation:///Users/ernest/GitHub/raja/lambda_handlers/control_plane/handler.py) - Token issuance endpoint

**Effort:** 2-3 hours

### Task 2: Create Authorizer Lambda

**What:** Lambda that validates identity, consults AVP, mints TAJs.

**Files to Create:**

- `lambda_handlers/rale_authorizer/handler.py`
- `lambda_handlers/rale_authorizer/requirements.txt`

**Flow:**

1. Parse USL from request path
2. Extract package name and hash
3. Validate user identity (IAM or OAuth)
4. Call AVP: `(principal, package_resource, action)`
5. On ALLOW: Mint TAJ with manifest hash
6. Cache in DynamoDB: `user_id#manifest_hash` → TAJ (5min TTL)
7. Return TAJ to client

**Terraform Resources:**

```hcl
resource "aws_lambda_function" "rale_authorizer" {
  function_name = "${var.stack_name}-rale-authorizer"
  role          = aws_iam_role.rale_authorizer_role.arn
  runtime       = "python3.12"
  handler       = "handler.handler"
  layers        = [aws_lambda_layer_version.raja.arn]

  environment {
    variables = {
      POLICY_STORE_ID = aws_verifiedpermissions_policy_store.raja.policy_store_id
      TAJ_CACHE_TABLE = aws_dynamodb_table.taj_cache.name
      JWT_SECRET_ARN  = aws_secretsmanager_secret.jwt.arn
    }
  }
}
```

**IAM Permissions:**

- AVP: `verifiedpermissions:IsAuthorized`
- DynamoDB: Read/Write on TAJ Cache table
- Secrets Manager: Read JWT secret

**Effort:** 1 day

### Task 3: Create Router Lambda

**What:** Lambda that validates TAJs, translates USLs, proxies to S3.

**Files to Create:**

- `lambda_handlers/rale_router/handler.py`
- `lambda_handlers/rale_router/requirements.txt`

**Flow:**

1. Validate TAJ signature using JWT secret
2. Extract manifest hash from TAJ claims
3. Check Manifest Cache for `manifest_hash`
4. If miss: Call `resolve_package_manifest(quilt_uri)` and cache
5. Parse logical key from USL
6. Prove membership: `(bucket, key) in manifest`
7. Construct physical S3 URI with version ID
8. Rewrite request headers/path
9. Sign with SigV4 using task IAM role
10. Forward to S3 via boto3

**Terraform Resources:**

```hcl
resource "aws_lambda_function" "rale_router" {
  function_name = "${var.stack_name}-rale-router"
  role          = aws_iam_role.rale_router_role.arn
  runtime       = "python3.12"
  handler       = "handler.handler"
  timeout       = 30
  memory_size   = 1024
  layers        = [aws_lambda_layer_version.raja.arn]

  environment {
    variables = {
      MANIFEST_CACHE_TABLE = aws_dynamodb_table.manifest_cache.name
      JWT_SECRET_ARN       = aws_secretsmanager_secret.jwt.arn
    }
  }
}
```

**Dependencies:**

- Layer must include `quilt3` for manifest resolution
- IAM role needs S3 read on all backing buckets

**Effort:** 1 day

### Task 4: Deploy Cache Tables

**What:** DynamoDB tables for manifest and TAJ caching.

**Terraform:**

```hcl
resource "aws_dynamodb_table" "manifest_cache" {
  name         = "${var.stack_name}-manifest-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "manifest_hash"

  attribute {
    name = "manifest_hash"
    type = "S"
  }
}

resource "aws_dynamodb_table" "taj_cache" {
  name         = "${var.stack_name}-taj-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "cache_key"

  attribute {
    name = "cache_key"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }
}
```

**Effort:** 1-2 hours

### Task 5: Modify Envoy Routing

**What:** Route requests to Authorizer vs Router based on TAJ presence.

**Options:**

#### Option A: External Processor (Recommended)

Add ext_proc filter to Envoy that calls a routing decision Lambda:

```yaml
http_filters:
  - name: envoy.filters.http.ext_proc
    typed_config:
      grpc_service:
        envoy_grpc:
          cluster_name: lambda_router_cluster
```

#### Option B: Lua HTTP Routing

Modify existing Lua to forward to Lambda URLs via HTTP:

```lua
if has_valid_taj(token) then
  forward_to_lambda(router_lambda_url)
else
  forward_to_lambda(authorizer_lambda_url)
end
```

#### Option C: API Gateway Routing (Cleanest)

Place API Gateway in front of Lambdas, Envoy forwards all requests to APIGW:

```text
Client → Envoy → API Gateway → Authorizer Lambda
                             └→ Router Lambda
```

**Recommendation:** Option C (API Gateway) for simplicity and managed routing.

**Effort:** 1 day (high uncertainty - Envoy configuration tricky)

### Task 6: Integration Tests

**What:** End-to-end validation of RALE flow.

**Test Scenarios:**

1. **Logical Read:**
   - Request USL without TAJ
   - Authorizer issues TAJ
   - Request USL with TAJ
   - Router translates and retrieves object

2. **Manifest Pinning:**
   - TAJ for package `v1` (hash `abc`)
   - Package updated to `v2` (hash `def`)
   - TAJ still accesses `v1` manifest
   - Verify TAJ cannot access `v2` objects

3. **Membership Validation:**
   - TAJ for `package1@hash1`
   - Request object not in package
   - Router denies (403)

4. **Cache Efficiency:**
   - First request: AVP call (cold)
   - Second request: Cached TAJ (no AVP)
   - Verify CloudWatch shows single AVP invocation

**Files to Create:**

- `tests/integration/test_rale_authorizer.py`
- `tests/integration/test_rale_router.py`
- `tests/integration/test_rale_end_to_end.py`

**Effort:** 1 day

## Terraform Deployment Summary

### New Resources

```hcl
# Lambdas
aws_lambda_function.rale_authorizer
aws_lambda_function.rale_router

# IAM Roles
aws_iam_role.rale_authorizer_role
aws_iam_role.rale_router_role

# DynamoDB Tables
aws_dynamodb_table.manifest_cache
aws_dynamodb_table.taj_cache

# Envoy Config Update
aws_ecs_task_definition.rajee (modify environment variables)
```

### Updated Resources

- Envoy task definition (routing logic)
- RAJA Lambda layer (add `quilt3` dependency)

### Outputs to Add

```hcl
output "rale_authorizer_arn" {
  value = aws_lambda_function.rale_authorizer.arn
}

output "rale_router_arn" {
  value = aws_lambda_function.rale_router.arn
}
```

## Success Criteria

### Functional

- [ ] Client requests USL without TAJ → Authorizer issues TAJ
- [ ] Client requests USL with TAJ → Router translates and proxies to S3
- [ ] Manifest pinning works (old TAJ uses old manifest)
- [ ] Membership validation denies unauthorized keys
- [ ] S3 API semantics preserved

### Performance

- [ ] Authorizer latency: < 200ms (includes AVP call)
- [ ] Router latency: < 50ms (cache hit)
- [ ] Manifest cache hit rate: > 95%
- [ ] TAJ cache hit rate: > 90%
- [ ] Zero AVP calls on Router path

### Security

- [ ] TAJ signature validated
- [ ] Expired TAJs rejected
- [ ] Invalid manifest hashes rejected
- [ ] Fail-closed on errors

## Implementation Order

1. Define TAJ structure (2-3 hours)
2. Create Authorizer Lambda (1 day)
3. Create Router Lambda (1 day)
4. Deploy cache tables (1-2 hours)
5. Modify Envoy routing (1 day)
6. Integration tests (1 day)
7. Performance testing (4-6 hours)

**Total: 5-6 days**

## Open Questions

1. **Envoy Routing:** External processor vs Lua vs API Gateway? (Affects complexity)
2. **Manifest Size:** DynamoDB 400KB limit - need S3 fallback for large packages?
3. **Multi-Region:** Should RALE support cross-region manifest resolution?
4. **USL Format:** Exact format for logical addresses? (e.g., `registry/pkg@hash/path` vs `pkg@hash:path`)

## References

- [RALE Article](https://ihack.us/2026/02/05/rale-raj-authorizing-logical-endpoint/) - Design and architecture
- [Diwan Stories](01-diwan-stories.md) - Client-side runtime (future)
- [Current Envoy Lua](../../infra/raja_poc/assets/envoy/authorize.lua) - RAJEE authorization logic
- [Package Resolver](../../lambda_handlers/package_resolver/handler.py) - Manifest utilities
