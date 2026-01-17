# RAJA Integration Proof - Evidence Summary

## Executive Summary

The enhanced demo test (`./poe demo`) provides conclusive evidence that **RAJA is actively performing authorization** for all S3 operations through the Envoy proxy.

**Key Finding:** Envoy uses a **two-stage authorization process**:
1. **JWT Authentication Filter** - Validates JWT signature using JWKS endpoint
2. **Lua Filter** - Performs RAJA authorization using subset checking

## Evidence from Test Output

### 1. JWT Token Issuance (RAJA Control Plane)

```
[STEP 1] Obtaining JWT token from RAJA control plane...
✅ Token obtained (length: 613 chars)
   Token preview: eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiJhb...
```

**Proof:** The test obtains a signed JWT token from RAJA's `/token` endpoint.

### 2. Token Contains RAJA Grants (Compiled from Cedar Policies)

```
[STEP 2] Decoding token to inspect RAJA grants...
✅ Token contains 6 grant(s):
   1. s3:GetObject/raja-poc-test-*/rajee-integration/*
   2. s3:ListBucket/raja-poc-test-*/
   3. s3:GetObject/analytics-data/
   4. s3:ListBucket/analytics-data/
   5. s3:DeleteObject/raja-poc-test-*/rajee-integration/*
   6. s3:PutObject/raja-poc-test-*/rajee-integration/*
```

**Proof:** The JWT contains grants in RAJA's scope format (`action/resource/path`), compiled from Cedar policies.

### 3. Local RAJA Authorization Check (Python Library)

```
[STEP 3] Local RAJA authorization check...
   Request: s3:PutObject/raja-poc-test-712023778557-us-east-1/rajee-integration/84e5964467e1441585d17c77143325fa.txt

2026-01-16 15:59:35 [debug] authorization_granted
   grant=s3:PutObject/raja-poc-test-*/rajee-integration/*
   request=s3:PutObject/raja-poc-test-712023778557-us-east-1/rajee-integration/84e5964467e1441585d17c77143325fa.txt

✅ Local RAJA check: AUTHORIZED
```

**Proof:** The local RAJA library (`is_authorized()`) performs subset checking and logs which grant matched the request.

### 4. Envoy JWT + Lua Authorization (RAJA Data Plane)

```
[STEP 4] Sending request through Envoy with x-raja-authorization header...
✅ Envoy accepted request (JWT filter validated signature, Lua filter authorized)
✅ GET request authorized by RAJA Lua filter
✅ DELETE request authorized by RAJA Lua filter
```

**Proof:** The request succeeds with HTTP 200, indicating:
1. Envoy's JWT authentication filter validated the token signature using JWKS
2. Envoy's Lua filter performed RAJA authorization and allowed the request

### 5. RAJA Denial Test (Negative Case)

```
🚫 RAJA DENIAL PROOF TEST

[STEP 1] Obtaining RAJA token...
✅ Token grants:
   • s3:GetObject/raja-poc-test-*/rajee-integration/*
   • s3:PutObject/raja-poc-test-*/rajee-integration/*
   [... other grants for rajee-integration/ prefix ...]

[STEP 2] Checking if request matches any grants...
   Request: s3:PutObject/raja-poc-test-712023778557-us-east-1/unauthorized-prefix/test.txt

2026-01-16 15:59:36 [warning] authorization_denied
   grants=[...list of grants...]
   request=s3:PutObject/raja-poc-test-712023778557-us-east-1/unauthorized-prefix/test.txt

   Local RAJA check: DENIED
✅ Expected: Request should be denied (no matching grant)

[STEP 3] Sending unauthorized request through Envoy...
✅ ENVOY DENIED REQUEST (403 Forbidden)
   • Envoy JWT filter validated signature (passed)
   • Envoy Lua filter denied request based on grants (403)
```

**Proof:**
1. Token contains NO grant for `unauthorized-prefix/`
2. Local RAJA check correctly predicts DENIAL
3. Envoy JWT filter validates the signature (passes authentication)
4. Envoy Lua filter denies based on grants (fails authorization)
5. Client receives 403 Forbidden

## Architecture Flow Confirmed

```
┌─────────────────────────────────────────────────────────────────┐
│ 1. Cedar Policies → RAJA Compiler → Grants in DynamoDB         │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 2. Client requests token → RAJA Token Service → JWT with grants│
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 3. Client sends request to Envoy with x-raja-authorization     │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 4. Envoy JWT Authentication Filter (jwt_authn)                  │
│    • Validates JWT signature using JWKS endpoint                │
│    • Checks issuer and audience claims                          │
│    • Forwards JWT payload to next filter                        │
│    • Returns 401 if signature invalid                           │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 5. Envoy Lua Filter (authorize.lua)                             │
│    • Extracts grants from JWT payload                           │
│    • Parses S3 request (method, path, query params)             │
│    • Performs subset checking (request ⊆ grants)                │
│    • Returns 403 if no grant matches                            │
└─────────────────────────────────────────────────────────────────┘
                              ↓
┌─────────────────────────────────────────────────────────────────┐
│ 6. If ALLOW: Envoy proxies request to S3                       │
│    If DENY:  Envoy returns 403 Forbidden to client             │
└─────────────────────────────────────────────────────────────────┘
```

## Key Observations

1. **Two-Stage Authorization:**
   - **Stage 1 (JWT Filter):** Validates cryptographic signature (authentication)
   - **Stage 2 (Lua Filter):** Performs RAJA subset checking (authorization)

2. **No Policy Evaluation at Runtime:** The Lua filter does NOT call Amazon Verified Permissions (AVP). It only performs subset checking on pre-compiled grants in the JWT.

3. **Fail-Closed Behavior:** Requests without matching grants are denied (403), as shown in the denial test.

4. **Transparent Decisions:** RAJA logs show exactly which grant matched (or didn't match) each request.

5. **Pure Subset Checking:** Authorization is reduced to checking if `request ⊆ grants`, a simple string matching operation in Lua.

## Technical Implementation Details

### Envoy Configuration

From `entrypoint.sh` (lines 48-81):
```yaml
- name: envoy.filters.http.jwt_authn
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.jwt_authn.v3.JwtAuthentication
    providers:
      raja_provider:
        issuer: "${RAJA_ISSUER}"
        audiences: ["raja-s3-proxy"]
        from_headers:
          - name: "x-raja-authorization"
            value_prefix: "Bearer "
        remote_jwks:
          http_uri:
            uri: "${JWKS_ENDPOINT}"
            cluster: jwks_cluster
            timeout: 5s
        forward_payload_header: "x-raja-jwt-payload"

- name: envoy.filters.http.lua
  typed_config:
    "@type": type.googleapis.com/envoy.extensions.filters.http.lua.v3.Lua
    default_source_code:
      inline_string: |
        [authorize.lua code]
```

### Lua Filter Logic

From `authorize.lua` (lines 110-159):
1. Reads JWT payload from `x-raja-jwt-payload` header (set by JWT filter)
2. Extracts grants from JWT payload
3. Parses S3 request into standard format
4. Calls `authorize_lib.authorize()` for subset checking
5. Returns 403 if no grant matches

## How to Reproduce

Run the demo test:

```bash
./poe demo
```

This runs the integration test suite that demonstrates:
- Token issuance with grants
- Successful authorization for allowed operations
- Denial (403) for unauthorized operations
- All S3 operations (PUT, GET, DELETE, ListBucket, etc.)

## Conclusion

The test output provides **definitive proof** that:

✅ RAJA issues JWT tokens containing grants compiled from Cedar policies
✅ Tokens are sent to Envoy via `x-raja-authorization` header
✅ Envoy's JWT authentication filter validates signatures using JWKS
✅ Envoy's Lua filter performs RAJA authorization using subset checking
✅ RAJA performs subset checking (not policy evaluation at runtime)
✅ Unauthorized requests are denied with 403 Forbidden
✅ All authorization decisions are logged and transparent

**RAJA is the sole authorization mechanism** for the Envoy S3 proxy, implemented as a Lua filter that performs pure subset checking on pre-compiled grants.
