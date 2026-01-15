# Implementation Plan: Envoy JWT Mechanistic Authorization for RAJEE

## Goal

Implement the mechanistic "Request ⊆ Authority" checking model in Envoy, where:

- JWTs contain S3 prefix-based grants (e.g., `s3:GetObject/bucket/uploads/`)
- Envoy performs pure subset/prefix checking without policy evaluation
- No external authorization service calls on the hot path
- Aligns with the RAJA vision: "Stop asking the data plane to think"

## Current State Analysis

### What Exists

✅ **Authorization logic**: `src/raja/rajee/authorizer.py` implements prefix-based S3 authorization
✅ **Request parsing**: HTTP → S3 action mapping fully implemented
✅ **Token structure**: JWT with `grants` claim for prefix-based authorization
✅ **Authorizer sidecar code**: FastAPI service in `lambda_handlers/authorizer/` (not deployed)
✅ **Envoy infrastructure**: ECS Fargate deployment with health checks

### Critical Gaps

❌ **No JWT validation in Envoy**: Current setup is an open proxy
❌ **AUTH_DISABLED=true by default**: Fail-open mode with no security
❌ **No authorization filters configured**: Envoy config has empty `__AUTH_FILTER__` placeholder
❌ **Claim format mismatch**: Control plane issues `scopes` but authorizer expects `grants`
❌ **Stack dependency removed**: RajeeEnvoyStack no longer gets JWT secret from ServicesStack

## Architecture Decision: Envoy Lua Filter

### Why Not ext_authz (Rejected Approach)

The blog post explicitly states Envoy must NOT call external services per-request:
> "Envoy must never call RAJA or any policy engine per-request. Placing decision logic in the data plane would reintroduce latency in the hot path"

### Why Lua Filter (Chosen Approach)

**Envoy Lua Filter** enables mechanistic checking directly in Envoy:

- ✅ Pure subset/prefix checking in the data plane
- ✅ No external service calls (zero network latency)
- ✅ JWT validation using Envoy's built-in `jwt_authn` filter
- ✅ Custom scope checking logic in Lua
- ✅ Aligns with "compiled authorization" vision

**Flow:**

```
Request → JWT Authn Filter → Lua Filter → S3 Proxy
              │                   │
              ▼                   ▼
         Validate JWT      Extract grants from JWT
         Check expiry      Parse S3 request
         Set metadata      Prefix match check
                          ALLOW/DENY
```

## Implementation Plan

### Phase 1: Add JWT Authentication Filter

**File:** `infra/raja_poc/assets/envoy/envoy.yaml.tmpl`

Add `jwt_authn` filter configuration:

- Provider: Raja token service
- JWKS endpoint: From ServicesStack API
- Audiences: `["raja-s3-proxy"]`
- Issuer validation
- Forward JWT payload to downstream filters

**Configuration structure:**

```yaml
http_filters:
  - name: envoy.filters.http.jwt_authn
    typed_config:
      "@type": type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication
      providers:
        raja_provider:
          issuer: "${RAJA_ISSUER}"
          audiences:
            - "raja-s3-proxy"
          remote_jwks:
            http_uri:
              uri: "${JWKS_ENDPOINT}"
              cluster: jwks_cluster
              timeout: 5s
            cache_duration: 600s
          forward: true
          forward_payload_header: "x-raja-jwt-payload"
      rules:
        - match:
            prefix: "/"
          requires:
            provider_name: raja_provider
```

**Benefits:**

- JWT signature validation (cryptographic)
- Expiration checking (automatic)
- Payload extraction (for Lua filter)

### Phase 2: Implement Lua Authorization Filter

**New file:** `infra/raja_poc/assets/envoy/authorize.lua`

Implement Lua script with:

1. **Extract grants from JWT**
   - Read JWT payload from request header (`x-raja-jwt-payload`)
   - Parse JSON to extract `grants` array claim

2. **Parse S3 request**
   - Extract HTTP method from `request_handle:headers():get(":method")`
   - Extract path from `request_handle:headers():get(":path")`
   - Parse query parameters for ListBucket detection
   - Map to S3 action (GetObject, PutObject, etc.)
   - Construct request string: `s3:GetObject/bucket/key`

3. **Prefix matching authorization**
   - For each grant in token: check if `string.find(request_string, grant, 1, true) == 1`
   - Return 403 if no match (fail-closed)
   - Allow if any grant matches

4. **Emit metrics**
   - Log authorization decisions to Envoy log
   - Set response headers for debugging (`x-raja-decision`, `x-raja-reason`)

**Pseudocode:**

```lua
function envoy_on_request(request_handle)
  -- Extract JWT payload
  local jwt_header = request_handle:headers():get("x-raja-jwt-payload")
  if not jwt_header then
    request_handle:respond({[":status"] = "401"}, "Missing JWT")
    return
  end

  local jwt_payload = parse_json(jwt_header)
  local grants = jwt_payload.grants or {}

  -- Parse S3 request
  local method = request_handle:headers():get(":method")
  local path = request_handle:headers():get(":path")
  local request_string = construct_request_string(method, path)

  -- Prefix match authorization
  for _, grant in ipairs(grants) do
    if string.find(request_string, grant, 1, true) == 1 then
      request_handle:logInfo("ALLOW: " .. request_string .. " matches " .. grant)
      return  -- Allow request
    end
  end

  -- Deny by default
  request_handle:respond({[":status"] = "403"}, "Insufficient grants")
end
```

**Reference implementation:** `src/raja/rajee/authorizer.py` (Python → Lua port)

### Phase 3: Update Envoy Configuration Template

**File:** `infra/raja_poc/assets/envoy/entrypoint.sh`

Replace the current `__AUTH_FILTER__` injection logic:

**When AUTH_DISABLED=true:**

- Inject empty filter (open proxy, for testing)

**When AUTH_DISABLED=false:**

- Inject JWT authn filter + Lua filter chain
- Enable full authorization checking

**Updated logic:**

```bash
if [ "$AUTH_DISABLED_VALUE" = "true" ]; then
  AUTH_FILTER=""
else
  AUTH_FILTER=$(cat <<'EOF'
                  - name: envoy.filters.http.jwt_authn
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication
                      providers:
                        raja_provider:
                          issuer: "${RAJA_ISSUER}"
                          audiences: ["raja-s3-proxy"]
                          remote_jwks:
                            http_uri:
                              uri: "${JWKS_ENDPOINT}"
                              cluster: jwks_cluster
                          forward: true
                          forward_payload_header: "x-raja-jwt-payload"
                      rules:
                        - match: {prefix: "/"}
                          requires: {provider_name: raja_provider}
                  - name: envoy.filters.http.lua
                    typed_config:
                      "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua
                      inline_code: |
                        $(cat /etc/envoy/authorize.lua)
EOF
)
fi
```

### Phase 4: Fix Token Claim Format

**File:** `src/raja/server/routers/control_plane.py`

Update `/token` endpoint to support both formats:

- Add `token_type` parameter: `"raja"` (scopes) or `"rajee"` (grants)
- For RAJEE tokens: convert scopes to prefix-based grants format
- Return JWT with `grants` claim instead of `scopes`

**API changes:**

```python
class TokenRequest(BaseModel):
    principal: str
    token_type: str = "raja"  # "raja" or "rajee"
    # ... existing fields

@router.post("/token")
def issue_token(payload: TokenRequest, ...) -> dict[str, Any]:
    scopes = item.get("scopes", [])

    if payload.token_type == "rajee":
        # Convert scopes to grants format
        grants = convert_scopes_to_grants(scopes)
        token = create_token_with_grants(
            subject=payload.principal,
            grants=grants,
            ttl=TOKEN_TTL,
            secret=secret,
        )
        return {"token": token, "principal": payload.principal, "grants": grants}
    else:
        # Existing scopes-based token
        token = create_token(...)
        return {"token": token, "principal": payload.principal, "scopes": scopes}
```

**Backward compatibility:** Keep existing `scopes` format for non-S3 use cases

### Phase 5: Pass JWKS Endpoint to RajeeEnvoyStack

**File:** `infra/raja_poc/app.py`

Update stack instantiation:

```python
rajee_envoy_stack = RajeeEnvoyStack(
    app,
    "RajeeEnvoyStack",
    jwks_endpoint=services_stack.api_url + "/.well-known/jwks.json",
    raja_issuer=f"https://{services_stack.api_url}",
)
# No add_dependency() - runtime config, not CloudFormation dependency
```

**File:** `infra/raja_poc/stacks/rajee_envoy_stack.py`

Add constructor parameters:

```python
def __init__(
    self,
    scope: Construct,
    construct_id: str,
    *,
    jwks_endpoint: str | None = None,
    raja_issuer: str | None = None,
    certificate_arn: str | None = None,
    **kwargs: object,
) -> None:
```

Pass as environment variables to Envoy container:

```python
envoy_container = task_definition.add_container(
    "EnvoyProxy",
    environment={
        "ENVOY_LOG_LEVEL": "info",
        "AUTH_DISABLED": auth_disabled.value_as_string,
        "JWKS_ENDPOINT": jwks_endpoint or "",
        "RAJA_ISSUER": raja_issuer or "",
    },
    ...
)
```

### Phase 6: Add JWKS Endpoint to Control Plane

**File:** `src/raja/server/routers/control_plane.py`

Implement `GET /.well-known/jwks.json`:

```python
@router.get("/.well-known/jwks.json")
def get_jwks() -> dict[str, Any]:
    """Return JWKS for JWT signature verification."""
    # Read JWT secret from environment
    secret = get_jwt_secret()

    # Convert symmetric key to JWKS format
    # For HS256, we need to expose the key in JWKS format
    # Note: In production, should use asymmetric keys (RS256)

    return {
        "keys": [
            {
                "kty": "oct",
                "kid": "raja-jwt-key",
                "alg": "HS256",
                "k": base64.urlsafe_b64encode(secret.encode()).decode(),
            }
        ]
    }
```

**Security note:** HMAC keys in JWKS require exposing the secret. For production, migrate to asymmetric RS256 keys where only the public key is exposed.

### Phase 7: Update AUTH_DISABLED Default

**File:** `infra/raja_poc/stacks/rajee_envoy_stack.py`

Change CloudFormation parameter default:

```python
auth_disabled = CfnParameter(
    self,
    "AUTH_DISABLED",
    type="String",
    default="false",  # ← Enable authorization by default
    allowed_values=["true", "false"],
    description="Disable authorization checks (only for testing/bootstrap).",
)
```

### Phase 8: Update Dockerfile to Include Lua Script

**File:** `infra/raja_poc/assets/envoy/Dockerfile`

Add Lua script to image:

```dockerfile
FROM envoyproxy/envoy:v1.28-latest

# Install curl for health checks
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

COPY infra/raja_poc/assets/envoy/envoy.yaml.tmpl /etc/envoy/envoy.yaml.tmpl
COPY infra/raja_poc/assets/envoy/entrypoint.sh /usr/local/bin/entrypoint.sh
COPY infra/raja_poc/assets/envoy/authorize.lua /etc/envoy/authorize.lua

RUN chmod +x /usr/local/bin/entrypoint.sh

ENV AUTH_DISABLED=true

CMD ["/usr/local/bin/entrypoint.sh"]
```

## Critical Files to Modify

1. ✏️ **infra/raja_poc/assets/envoy/envoy.yaml.tmpl** - Add JWT + Lua filters, JWKS cluster
2. ✨ **infra/raja_poc/assets/envoy/authorize.lua** - NEW FILE - Lua authorization logic
3. ✏️ **infra/raja_poc/assets/envoy/entrypoint.sh** - Update filter injection logic
4. ✏️ **infra/raja_poc/assets/envoy/Dockerfile** - Copy Lua script
5. ✏️ **infra/raja_poc/stacks/rajee_envoy_stack.py** - Add JWKS/issuer parameters
6. ✏️ **infra/raja_poc/app.py** - Pass JWKS endpoint to stack
7. ✏️ **src/raja/server/routers/control_plane.py** - Add JWKS endpoint, support `grants` claim
8. ✏️ **src/raja/token.py** - Add optional `grants` claim support

## Testing Strategy

### Unit Tests

**New file:** `tests/unit/test_lua_authorization.py`

Test Lua logic by simulating:

- JWT payload parsing
- Request string construction
- Prefix matching logic

**Approach:** Use Python to simulate Lua behavior, or use a Lua test framework

### Integration Tests

**File:** `tests/integration/test_rajee_envoy_authorization.py`

Test scenarios:

1. **Deploy both stacks**: RajaServicesStack + RajeeEnvoyStack
2. **Get token with grants**: Call `/token` endpoint with `token_type=rajee`
3. **Test authorized request**: GET with valid token → 200
4. **Test unauthorized request**: GET with insufficient grants → 403
5. **Test missing token**: GET without token → 401
6. **Test expired token**: GET with expired token → 401
7. **Test prefix matching**:
   - Grant `s3:GetObject/bucket/uploads/` allows `/bucket/uploads/file.txt`
   - Grant `s3:GetObject/bucket/uploads/` denies `/bucket/docs/file.txt`
8. **Test multipart uploads**: Verify multipart workflow with single token

### Local Testing

**Script:** `infra/test-docker.sh`

Update to test authorization:

```bash
#!/bin/bash
set -e

# Build Envoy image
docker build -t rajee-envoy -f infra/raja_poc/assets/envoy/Dockerfile .

# Generate test JWT secret
export JWT_SECRET="test-secret-key"

# Start mock JWKS server (or use local file)
# Start Envoy with AUTH_DISABLED=false
docker run -d --name rajee-test \
  -e AUTH_DISABLED=false \
  -e JWKS_ENDPOINT="http://host.docker.internal:8000/.well-known/jwks.json" \
  -e RAJA_ISSUER="https://test.local" \
  -p 10000:10000 \
  rajee-envoy

# Generate test token with grants
# Test authorized request
# Test unauthorized request

# Cleanup
docker stop rajee-test
docker rm rajee-test
```

## Stack Dependency Resolution

**No CloudFormation dependency needed:**

- JWKS endpoint passed as HTTP URL string
- Envoy fetches JWKS at runtime (not deploy time)
- RajeeEnvoyStack remains independently deployable
- ServicesStack must be running for JWT validation (runtime dependency)

**For development/testing:**

- Can set `AUTH_DISABLED=true` to test Envoy without ServicesStack
- Production: Both stacks deployed, `AUTH_DISABLED=false`

**CloudFormation outputs needed:**

```python
# services_stack.py
CfnOutput(self, "ApiUrl", value=self.api_url)
CfnOutput(self, "JwksEndpoint", value=f"{self.api_url}/.well-known/jwks.json")
```

## Alignment with RAJA Vision

✅ **"Stop asking the data plane to think"** - Envoy does pure prefix matching, no policy evaluation

✅ **Authorization compilation** - Grants are compiled into JWTs by control plane

✅ **Request ⊆ Authority** - Lua filter checks subset relationship via prefix matching

✅ **No hot-path service calls** - All checking is local to Envoy

✅ **Fail-closed by default** - Unknown requests DENY, `AUTH_DISABLED=false` default

✅ **Monotonic tokens** - JWTs remain valid until expiry (no revocation complexity)

✅ **Pure mechanistic checking** - No policy interpretation, just string prefix matching

## Verification Steps

### 1. Deploy Infrastructure

```bash
npx cdk deploy RajaServicesStack RajeeEnvoyStack
```

### 2. Get RAJEE Token

```bash
# Get token with S3 grants
TOKEN=$(curl -X POST https://${API_URL}/token \
  -H "Content-Type: application/json" \
  -d '{
    "principal": "User::alice",
    "token_type": "rajee",
    "scopes": ["s3:GetObject/test-bucket/uploads/"]
  }' | jq -r '.token')
```

### 3. Test Authorized Request

```bash
curl -v -H "Authorization: Bearer $TOKEN" \
  https://${RAJEE_URL}/test-bucket/uploads/file.txt

# Expected: 200 OK (proxied to S3)
# Expected headers:
#   x-raja-decision: allow
#   x-raja-reason: matched grant s3:GetObject/test-bucket/uploads/
```

### 4. Test Unauthorized Request

```bash
curl -v -H "Authorization: Bearer $TOKEN" \
  https://${RAJEE_URL}/test-bucket/docs/file.txt

# Expected: 403 Forbidden
# Expected headers:
#   x-raja-decision: deny
#   x-raja-reason: no matching grant
```

### 5. Test Missing Token

```bash
curl -v https://${RAJEE_URL}/test-bucket/file.txt

# Expected: 401 Unauthorized
# Expected: JWT validation error
```

### 6. Check CloudWatch Metrics

```bash
aws cloudwatch get-metric-statistics \
  --namespace RAJEE \
  --metric-name AuthorizationAllow \
  --statistics Sum \
  --start-time $(date -u -d '5 minutes ago' +%Y-%m-%dT%H:%M:%S) \
  --end-time $(date -u +%Y-%m-%dT%H:%M:%S) \
  --period 60

# Should show increments for allowed requests
```

### 7. Test Multipart Upload Flow

```bash
# Initiate multipart
UPLOAD_ID=$(curl -X POST -H "Authorization: Bearer $TOKEN" \
  "https://${RAJEE_URL}/test-bucket/large-file.bin?uploads" \
  | grep UploadId | cut -d'>' -f2 | cut -d'<' -f1)

# Upload part
curl -X PUT -H "Authorization: Bearer $TOKEN" \
  --data-binary @part1.bin \
  "https://${RAJEE_URL}/test-bucket/large-file.bin?partNumber=1&uploadId=$UPLOAD_ID"

# Complete multipart
curl -X POST -H "Authorization: Bearer $TOKEN" \
  --data '<CompleteMultipartUpload>...</CompleteMultipartUpload>' \
  "https://${RAJEE_URL}/test-bucket/large-file.bin?uploadId=$UPLOAD_ID"

# All should succeed with single token
```

## Risks and Mitigations

### Risk 1: Lua Filter Complexity

**Risk:** Lua script has bugs or performance issues

**Mitigation:**

- Port tested Python logic from `rajee/authorizer.py`
- Comprehensive unit tests for Lua script
- Performance benchmark against ext_authz approach
- Keep Lua logic minimal (pure prefix matching)

### Risk 2: JWKS Endpoint Availability

**Risk:** ServicesStack unavailable → JWT validation fails

**Mitigation:**

- Envoy caches JWKS responses (600s TTL configurable)
- Can fall back to static JWKS in config for emergencies
- Monitor JWKS endpoint health
- Consider CDN for JWKS distribution

### Risk 3: HMAC Key in JWKS

**Risk:** Exposing HMAC secret in JWKS is insecure

**Mitigation:**

- **Phase 1:** Use HS256 with JWKS for MVP (document security caveat)
- **Phase 2:** Migrate to RS256 asymmetric keys
  - Private key stays in ServicesStack
  - Public key in JWKS (safe to expose)
  - Update token signing to use RS256

### Risk 4: Performance Impact of Lua

**Risk:** Lua adds latency to hot path

**Mitigation:**

- Benchmark: Measure p50/p99 latency with Lua filter enabled
- Compare to ext_authz baseline
- Lua is compiled and optimized for simple operations
- Profile and optimize Lua code if needed

### Risk 5: Token Format Migration

**Risk:** Breaking existing deployments with format change

**Mitigation:**

- Support both `scopes` and `grants` claims during transition
- Document migration path for existing deployments
- Provide conversion utility for tokens
- Version API endpoints if needed

### Risk 6: Envoy Configuration Complexity

**Risk:** Complex filter chain is hard to debug

**Mitigation:**

- Comprehensive logging at each filter stage
- Response headers expose decision reasoning
- Envoy admin interface for live debugging
- Integration tests cover all edge cases

## Success Criteria

1. ✅ RajeeEnvoyStack deploys with `AUTH_DISABLED=false`
2. ✅ JWT validation works (401 for invalid/expired tokens)
3. ✅ Prefix-based authorization works (403 for insufficient grants)
4. ✅ Authorized requests proxy to S3 successfully
5. ✅ CloudWatch metrics show authorization decisions
6. ✅ No external authorization service calls (pure Envoy checking)
7. ✅ Integration tests pass end-to-end
8. ✅ Performance meets requirements (p99 < 10ms for authorization check)
9. ✅ Documentation updated with architecture diagrams
10. ✅ Local testing script validates authorization flow

## Timeline Estimate

### Development

- **Phase 1:** JWT authn filter config - 2 hours
- **Phase 2:** Lua authorization script - 4 hours
- **Phase 3:** Entrypoint updates - 1 hour
- **Phase 4:** Token format support - 2 hours
- **Phase 5:** Stack parameter passing - 1 hour
- **Phase 6:** JWKS endpoint - 2 hours
- **Phase 7:** Configuration defaults - 0.5 hours
- **Phase 8:** Dockerfile updates - 0.5 hours

### Testing

- Unit tests - 2 hours
- Integration tests - 3 hours
- Local testing - 2 hours
- Performance benchmarking - 2 hours

### Documentation

- Architecture diagrams - 1 hour
- API documentation - 1 hour
- Migration guide - 1 hour

**Total: 24-28 hours** (3-4 days)

## Future Enhancements

### Phase 2: Asymmetric Keys (RS256)

**Why:** Eliminate HMAC secret exposure in JWKS

**Changes:**

1. Generate RSA key pair in ServicesStack
2. Store private key in Secrets Manager
3. Expose public key in JWKS endpoint
4. Update token signing to use RS256
5. Update Envoy JWT filter to use RS256

**Benefit:** Public key can be safely distributed via JWKS

### Phase 3: Token Caching in Envoy

**Why:** Reduce JWT validation overhead

**Implementation:**

- Envoy JWT cache (built-in)
- Short-lived cache (30-60s)
- Invalidate on 401 responses

### Phase 4: CloudWatch Metrics from Lua

**Why:** Better observability of authorization decisions

**Implementation:**

- Use Envoy metrics API from Lua
- Emit custom metrics (allow/deny counts)
- Track authorization latency

### Phase 5: Dynamic Grant Updates

**Why:** Support grant revocation without waiting for token expiry

**Implementation:**

- Envoy ext_proc filter for dynamic grant enrichment
- Check grant revocation list
- Maintain fail-closed semantics

## Documentation Updates

### New Documents

1. **specs/2-rajee/09-envoy-mechanistic-authorization-plan.md** (this document)
   - Complete implementation plan
   - Architecture decisions
   - Testing strategy

2. **specs/2-rajee/10-lua-filter-specification.md**
   - Detailed Lua filter specification
   - API contracts
   - Error handling

3. **specs/2-rajee/11-token-format-migration.md**
   - Migration path from `scopes` to `grants`
   - Backward compatibility strategy
   - Conversion utilities

### Updated Documents

1. **README.md**
   - Add RAJEE authorization architecture diagram
   - Document mechanistic checking model
   - Update quick start guide

2. **specs/2-rajee/08-authorization-gap-analysis.md**
   - Mark gaps as resolved
   - Document implementation approach
   - Link to this plan

3. **specs/2-rajee/03-rajee-design.md**
   - Update with Lua filter approach
   - Document JWKS integration
   - Add deployment architecture

## References

- Blog post: <https://ihack.us/2026/01/09/crowning-raj-how-resource-access-jwts-refactor-authorization-joyfully/>
- Envoy JWT filter: <https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/jwt_authn_filter>
- Envoy Lua filter: <https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/lua_filter>
- Existing authorizer: `src/raja/rajee/authorizer.py`
- Prefix design spec: `specs/2-rajee/02-prefix-authorization-design.md`
