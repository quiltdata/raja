# RAJA Failure Modes & Validation Gaps

## Purpose

This document identifies validation gaps in the RAJA authorization system. It documents failure modes that need testing WITHOUT proposing solutions - purely identification for prioritization.

**Current Validation State:**

- `./poe demo` validates happy path scenarios and one denial case
- Unit tests cover token security edge cases
- Lua tests validate scope matching logic
- **Gap:** Missing comprehensive security, policy, and enforcement failure mode testing

## Summary

| Category | Critical | High | Medium | Low |
|----------|----------|------|--------|-----|
| Token Security | 3 | 2 | 1 | 0 |
| Cedar Compilation | 2 | 3 | 2 | 0 |
| Scope Enforcement | 2 | 2 | 3 | 1 |
| Request Parsing | 1 | 2 | 2 | 0 |
| Cross-Component | 3 | 2 | 1 | 0 |
| Operational | 0 | 2 | 3 | 2 |

## 1. Token Security Failures

### 1.1 Expired Tokens [CRITICAL]

**Current Coverage:** Unit tests only ([tests/unit/test_token.py](../tests/unit/test_token.py))
**Missing from Demo:** No expired token test through Envoy
**Failure Mode:** Expired JWT passes validation in production
**Test Scenario:**

- Issue token with `exp` in the past
- Send S3 request through Envoy with expired token
- Expected: 401/403 rejection
- Actual behavior: Not tested

### 1.2 Invalid Signature [CRITICAL]

**Current Coverage:** Unit tests only ([tests/unit/test_token.py](../tests/unit/test_token.py))
**Missing from Demo:** No tampered token test through Envoy
**Failure Mode:** Token signed with wrong secret passes validation
**Test Scenario:**

- Issue token with different JWT secret
- Send S3 request through Envoy
- Expected: 401 rejection by JWT filter
- Actual behavior: Not tested

### 1.3 Malformed JWT [CRITICAL]

**Current Coverage:** Unit tests only ([tests/unit/test_token.py](../tests/unit/test_token.py))
**Missing from Demo:** No malformed token test through Envoy
**Failure Mode:** Invalid JWT format crashes Lua enforcer or passes through
**Test Scenarios:**

- Not a JWT: `"not.a.jwt"`
- Missing segments: `"header.payload"`
- Invalid base64: `"!!!.***.$$$"`
- Empty token: `""`

### 1.4 Missing/Empty Scopes [HIGH]

**Current Coverage:** Unit tests implicit ([tests/unit/test_enforcer.py](../tests/unit/test_enforcer.py))
**Missing from Demo:** No empty scopes test through Envoy
**Failure Mode:** Token without scopes claim grants access
**Test Scenarios:**

- JWT with no `scopes` claim
- JWT with `"scopes": []`
- JWT with `"scopes": null`

### 1.5 Token Claim Validation [HIGH]

**Current Coverage:** None
**Missing from Demo:** Subject/issuer/audience not validated
**Failure Mode:** Token from wrong issuer or for wrong audience accepted
**Test Scenarios:**

- Wrong `iss` claim
- Wrong `aud` claim
- Missing `sub` claim
- `sub` doesn't match principal in scopes

### 1.6 Token Revocation [MEDIUM]

**Current Coverage:** None (feature not implemented)
**Missing from Demo:** No token revocation mechanism
**Failure Mode:** Compromised tokens can't be revoked
**Gap:** No way to invalidate issued tokens before expiration

---

## 2. Cedar Policy Compilation Failures

### 2.1 Forbid Policies [CRITICAL]

**Current Coverage:** Compiler ignores forbid ([lambda_handlers/compiler/handler.py:52](../lambda_handlers/compiler/handler.py))
**Missing from Demo:** No deny/forbid policy tests
**Failure Mode:** Forbid policies are silently ignored
**Test Scenario:**

```cedar
forbid(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:DeleteObject",
  resource == Raja::S3Object::"protected/"
) when { resource in Raja::S3Bucket::"raja-poc-test-{{account}}-{{region}}" };
```

**Expected:** Compilation error or DENY decision
**Actual:** Policy ignored, access granted

### 2.2 Template Injection [CRITICAL]

**Current Coverage:** None
**Missing from Demo:** No malicious template input tests
**Failure Mode:** Unvalidated template variables allow privilege escalation
**Test Scenarios:**

- `{{evil}}` in bucket name
- `{{account}}{{account}}` double expansion
- Template in key component (should be rejected)
- Missing template variables (should error)

### 2.3 Complex When Clauses [HIGH]

**Current Coverage:** Only hierarchy checks ([tests/unit/test_cedar_parser.py](../tests/unit/test_cedar_parser.py))
**Missing from Demo:** No attribute/condition-based policies
**Failure Mode:** Conditions beyond hierarchy are ignored
**Test Scenarios:**

```cedar
when {
  resource in Raja::S3Bucket::"bucket" &&
  context.time < "2024-12-31"
}
```

**Gap:** Time-based or attribute-based conditions not compiled to scopes

### 2.4 Principal In Clauses [HIGH]

**Current Coverage:** None
**Missing from Demo:** No role-based or group-based policies
**Failure Mode:** Group membership policies fail to compile
**Test Scenario:**

```cedar
permit(
  principal in Raja::Role::"data-engineers",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"data/"
) when { resource in Raja::S3Bucket::"bucket" };
```

**Gap:** Role/group expansion not implemented

### 2.5 Action In Clauses [HIGH]

**Current Coverage:** None
**Missing from Demo:** No multi-action policies
**Failure Mode:** Policies with action groups don't compile correctly
**Test Scenario:**

```cedar
permit(
  principal == Raja::User::"alice",
  action in [Raja::Action::"s3:GetObject", Raja::Action::"s3:PutObject"],
  resource == Raja::S3Object::"shared/"
) when { resource in Raja::S3Bucket::"bucket" };
```

**Gap:** Single policy should expand to multiple scopes

### 2.6 Multiple In Clauses [MEDIUM]

**Current Coverage:** None
**Missing from Demo:** No multi-hierarchy policies
**Failure Mode:** Multiple parent constraints not supported
**Test Scenario:**

```cedar
when {
  resource in Raja::S3Bucket::"bucket-a" ||
  resource in Raja::S3Bucket::"bucket-b"
}
```

**Gap:** Parser may fail or produce incorrect scopes

### 2.7 Invalid Entity Hierarchies [MEDIUM]

**Current Coverage:** Schema validation implicit
**Missing from Demo:** No invalid hierarchy tests
**Failure Mode:** Wrong parent-child relationships accepted
**Test Scenario:**

```cedar
resource == Raja::S3Bucket::"bucket"
when { resource in Raja::S3Object::"key" }  // Backwards!
```

**Gap:** Should reject inverted hierarchy

---

## 3. Scope Enforcement Failures

### 3.1 Malformed Scope Format [CRITICAL]

**Current Coverage:** Lua parser returns error ([authorize_lib.lua:37-49](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No malformed scope tests through Envoy
**Failure Mode:** Scope parsing errors crash or allow access
**Test Scenarios:**

- Missing colons: `"S3Objectbucket/keyaction"`
- Extra colons: `"S3Object:bucket:key:action:extra"`
- Empty components: `"S3Object::s3:GetObject"`
- No action: `"S3Object:bucket/key"`

### 3.2 Bucket Prefix Matching [CRITICAL]

**Current Coverage:** Design doc says bucket must be exact ([01-bucket-object.md:81-89](01-bucket-object.md))
**Missing from Demo:** No validation that bucket prefixes are rejected
**Failure Mode:** Bucket prefix with trailing `-` incorrectly allows wildcard matching
**Test Scenarios:**

- Scope: `"S3Object:raja-poc-test-/key:s3:GetObject"` (trailing `-`)
- Request: bucket `"raja-poc-test-different-account/key"`
- Expected: DENY (bucket must be exact)
- Gap: Not validated in Lua enforcer

### 3.3 Special Characters in Resource IDs [HIGH]

**Current Coverage:** None
**Missing from Demo:** No special character handling tests
**Failure Mode:** Special chars break parsing or matching
**Test Scenarios:**

- Colon in key: `"S3Object:bucket/path:with:colons.txt:s3:GetObject"`
- Slash in bucket: `"S3Object:bucket/slash/key.txt:s3:GetObject"`
- URL encoding: `"S3Object:bucket/file%20name.txt:s3:GetObject"`
- Unicode: `"S3Object:bucket/文件.txt:s3:GetObject"`

### 3.4 Empty Bucket/Key Components [HIGH]

**Current Coverage:** Lua returns error ([authorize_lib.lua:92-94](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No empty component tests
**Failure Mode:** Empty bucket or key bypasses validation
**Test Scenarios:**

- Empty bucket: `"S3Object:/key:s3:GetObject"`
- Empty key: `"S3Object:bucket/:s3:GetObject"` (just trailing slash)
- Both empty: `"S3Object:/:s3:GetObject"`

### 3.5 Resource Type Mismatches [MEDIUM]

**Current Coverage:** Lua checks type ([authorize_lib.lua:83-84](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No type mismatch tests
**Failure Mode:** S3Object scope grants S3Bucket access
**Test Scenarios:**

- Granted: `"S3Object:bucket/key:s3:ListBucket"`
- Requested: `"S3Bucket:bucket:s3:ListBucket"`
- Expected: DENY (type mismatch)

### 3.6 Action Field Missing [MEDIUM]

**Current Coverage:** Implicit in format parsing
**Missing from Demo:** No missing action tests
**Failure Mode:** Scope without action grants access
**Test Scenario:**

- Scope: `"S3Object:bucket/key"`
- Should: Fail parsing, return error

### 3.7 Trailing Slash Ambiguity [MEDIUM]

**Current Coverage:** Design doc defines trailing `/` as prefix ([01-bucket-object.md:72-81](01-bucket-object.md))
**Missing from Demo:** No ambiguous trailing slash tests
**Failure Modes:**

- Does `"rajee-integration/"` match `"rajee-integration"` (no slash)?
- Does `"prefix"` (no slash) match `"prefix/file.txt"`?
- Does `"prefix/"` match `"prefix-other/file.txt"`?

### 3.8 Substring vs Prefix Matching [LOW]

**Current Coverage:** Lua uses `string.sub` for prefix ([authorize_lib.lua:17-19](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No substring attack tests
**Failure Mode:** Prefix logic is incorrect
**Test Scenario:**

- Granted: `"S3Object:bucket/pre/:s3:GetObject"`
- Requested: `"S3Object:bucket/prefix/file.txt:s3:GetObject"`
- Current behavior: DENY (correct - "prefix/" doesn't start with "pre/")
- But: `"pre"` (no slash) would match `"prefix"` - needs testing

---

## 4. Request Parsing Failures

### 4.1 Missing Bucket/Key [CRITICAL]

**Current Coverage:** Lua returns error ([authorize_lib.lua:197-199](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No missing component tests through Envoy
**Failure Mode:** Malformed S3 request bypasses authorization
**Test Scenarios:**

- Path: `"/"`
- Path: `"//key"` (double slash)
- Path: `"/bucket/"` (bucket-only with trailing slash)

### 4.2 Query Parameter Injection [HIGH]

**Current Coverage:** Lua parses query string ([authorize_lib.lua:117-131](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No injection tests
**Failure Mode:** Malicious query params change detected action
**Test Scenarios:**

- `"?versionId=x&versionId=y"` (duplicate params)
- `"?uploadId=x&uploads="` (conflicting multipart params)
- `"?list-type=2&location="` (conflicting bucket operations)

### 4.3 Unknown S3 Actions [HIGH]

**Current Coverage:** Lua returns nil ([authorize_lib.lua:186](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No unknown action tests
**Failure Mode:** Unknown action returns nil, may allow or crash
**Test Scenarios:**

- Unimplemented operations: `GetObjectAcl`, `GetObjectTagging`
- Invalid methods: `PATCH /bucket/key`
- Missing query params: `POST /bucket/key` (no uploadId or uploads)

### 4.4 Path Traversal [MEDIUM]

**Current Coverage:** None
**Missing from Demo:** No path traversal tests
**Failure Mode:** `../` in key escapes intended prefix
**Test Scenarios:**

- Key: `"uploads/../private/secret.txt"`
- Key: `"./uploads/file.txt"`
- Key with null bytes: `"uploads\x00admin/file.txt"`

### 4.5 Malformed Query Strings [MEDIUM]

**Current Coverage:** Lua parser handles some cases ([authorize_lib.lua:117-131](../infra/raja_poc/assets/envoy/authorize_lib.lua))
**Missing from Demo:** No malformed query tests
**Failure Mode:** Parser crashes or returns unexpected results
**Test Scenarios:**

- `"?&&&"`
- `"?=value"` (no key)
- `"?key=value=extra"`
- `"?key="` (empty value)

---

## 5. Cross-Component Validation Gaps

### 5.1 Cedar → Scopes Traceability [CRITICAL]

**Current Coverage:** None
**Missing from Demo:** No validation that Cedar policies produce expected scopes
**Failure Mode:** Compiler silently drops policies or generates wrong scopes
**Gap:** No test that compares:

1. Cedar policy file content
2. Compiled scopes in DynamoDB
3. Scopes in issued token
4. Enforcement decision

### 5.2 Policy Updates vs Existing Tokens [CRITICAL]

**Current Coverage:** None (feature not designed)
**Missing from Demo:** No policy versioning tests
**Failure Mode:** Old tokens with outdated scopes still valid after policy changes
**Gap:** No token invalidation mechanism when policies change
**Test Scenario:**

1. Issue token with scopes from policy A
2. Update policy A to remove permissions
3. Old token still grants removed permissions

### 5.3 Scope Deduplication [CRITICAL]

**Current Coverage:** Compiler comment mentions it ([lambda_handlers/compiler/handler.py:71](../lambda_handlers/compiler/handler.py))
**Missing from Demo:** No validation that duplicate scopes are merged
**Failure Mode:** Multiple policies create duplicate scopes, token bloat
**Test Scenario:**

- Two policies grant same scope
- Expected: Single scope in token
- Actual: Not validated

### 5.4 Template Expansion Context [HIGH]

**Current Coverage:** Compiler receives account/region ([lambda_handlers/compiler/handler.py](../lambda_handlers/compiler/handler.py))
**Missing from Demo:** No validation of template expansion correctness
**Failure Mode:** Templates expand to wrong values in different environments
**Gap:** No test that verifies:

- `{{account}}` expands to correct AWS account ID
- `{{region}}` expands to correct region
- Unset variables cause compilation errors

### 5.5 Principal-to-Scopes Mapping [HIGH]

**Current Coverage:** DynamoDB stores principal→scopes
**Missing from Demo:** No validation that correct scopes are retrieved for principal
**Failure Mode:** Token issued with scopes for different principal
**Test Scenario:**

1. Compile policies for `"user-a"` and `"user-b"`
2. Request token for `"user-a"`
3. Token contains scopes for `"user-b"`

### 5.6 AVP Policy Store Consistency [MEDIUM]

**Current Coverage:** Load script uploads policies ([scripts/load_policies.py](../scripts/load_policies.py))
**Missing from Demo:** No validation that AVP store matches policy files
**Failure Mode:** AVP store is stale or has extra policies
**Gap:** No diff checking between local files and AVP store

---

## 6. Operational Validation Gaps

### 6.1 Authorization Decision Logging [HIGH]

**Current Coverage:** None
**Missing from Demo:** No validation that decisions are logged
**Failure Mode:** Security incidents undetectable due to missing audit trail
**Gap:** No test that verifies:

- ALLOW decisions logged with granted scope
- DENY decisions logged with reason
- Token principal logged
- Timestamp and request details logged

### 6.2 Authorization Performance [HIGH]

**Current Coverage:** None
**Missing from Demo:** No latency tests
**Failure Mode:** Lua enforcer adds unacceptable latency to S3 requests
**Gap:** No measurement of:

- P50/P99 authorization latency
- Latency with 1/10/100/1000 scopes in token
- Latency with deeply nested key paths

### 6.3 Concurrent Requests [MEDIUM]

**Current Coverage:** None
**Missing from Demo:** No concurrent authorization tests
**Failure Mode:** Race conditions in enforcement logic
**Gap:** No test with:

- Multiple simultaneous requests with same token
- Different tokens for same principal
- Concurrent policy updates during enforcement

### 6.4 Large Token Scopes [MEDIUM]

**Current Coverage:** None
**Missing from Demo:** No scale tests
**Failure Mode:** Large tokens exceed JWT size limits or cause performance degradation
**Gap:** No test with:

- 100 scopes in token
- 1000 scopes in token
- 10,000 scopes in token (max AWS token size)

### 6.5 Envoy Lua Memory Limits [MEDIUM]

**Current Coverage:** None
**Missing from Demo:** No memory tests
**Failure Mode:** Lua enforcer OOMs on large tokens
**Gap:** No validation of Envoy Lua memory constraints

### 6.6 Error Response Formats [MEDIUM]

**Current Coverage:** Integration tests check status codes
**Missing from Demo:** No validation of error response bodies
**Failure Mode:** S3 clients receive non-standard error responses
**Gap:** No test that validates:

- 403 responses include S3-compatible XML error
- Error codes match S3 API (`AccessDenied`, `InvalidToken`)
- Error messages don't leak security details

### 6.7 Health Check Validation [LOW]

**Current Coverage:** None
**Missing from Demo:** No health check tests
**Failure Mode:** Envoy serves traffic when authorization is broken
**Gap:** Health endpoint doesn't validate:

- Token service reachable
- JWT secret accessible
- Lua enforcer loaded correctly

---

## Current Test Coverage Matrix

| Component | Unit Tests | Lua Tests | Integration Tests | Demo |
|-----------|------------|-----------|-------------------|------|
| Token signature validation | ✅ | ❌ | ❌ | ❌ |
| Token expiration | ✅ | ❌ | ❌ | ❌ |
| Malformed tokens | ✅ | ❌ | ❌ | ❌ |
| Scope prefix matching | ✅ | ✅ | ✅ | ✅ |
| Bucket exact matching | ✅ | ✅ | ❌ | ❌ |
| Action equivalence | ✅ | ✅ | ✅ | ✅ |
| S3 request parsing | ❌ | ✅ | ❌ | ❌ |
| Multipart operations | ❌ | ✅ | ✅ | ✅ |
| Versioned operations | ❌ | ✅ | ✅ | ✅ |
| Cedar parsing | ✅ | ❌ | ❌ | ❌ |
| Template expansion | ✅ | ❌ | ✅ | ✅ |
| Forbid policies | ❌ | ❌ | ❌ | ❌ |
| Role-based access | ❌ | ❌ | ❌ | ❌ |
| Authorization logging | ❌ | ❌ | ❌ | ❌ |
| Performance/scale | ❌ | ❌ | ❌ | ❌ |
| Cross-component trace | ❌ | ❌ | ❌ | ❌ |

**Legend:**

- ✅ Tested
- ❌ Not tested

---

## Priority Recommendations

### Critical (Security Failures)

1. Expired/invalid tokens through Envoy
2. Forbid policy handling
3. Bucket prefix matching validation
4. Cedar→scopes→token traceability
5. Policy update vs token validity

### High (Authorization Correctness)

1. Token claim validation (iss, aud, sub)
2. Complex Cedar when clauses
3. Principal/action in clauses
4. Special characters in resource IDs
5. Authorization decision logging

### Medium (Edge Cases)

1. Malformed scopes/queries/paths
2. Template injection attacks
3. Resource type mismatches
4. Concurrent requests
5. Large token scale tests

### Low (Operational)

1. Substring vs prefix matching
2. Health check validation
3. Error response formats

---

## References

### Current Test Files

- Unit tests: [tests/unit/](../tests/unit/)
  - [test_token.py](../tests/unit/test_token.py) - Token validation
  - [test_enforcer.py](../tests/unit/test_enforcer.py) - Scope matching
  - [test_cedar_parser.py](../tests/unit/test_cedar_parser.py) - Cedar parsing
  - [test_compiler.py](../tests/unit/test_compiler.py) - Policy compilation

- Lua tests: [tests/lua/authorize_spec.lua](../tests/lua/authorize_spec.lua)

- Integration tests: [tests/integration/](../tests/integration/)
  - [test_rajee_envoy_bucket.py](../tests/integration/test_rajee_envoy_bucket.py) - Demo tests

### Implementation Files

- Lua enforcer: [infra/raja_poc/assets/envoy/authorize_lib.lua](../infra/raja_poc/assets/envoy/authorize_lib.lua)
- Python enforcer: [src/raja/enforcer.py](../src/raja/enforcer.py)
- Cedar parser: [src/raja/cedar/parser.py](../src/raja/cedar/parser.py)
- Compiler: [lambda_handlers/compiler/handler.py](../lambda_handlers/compiler/handler.py)

### Design Specs

- [01-bucket-object.md](01-bucket-object.md) - Hierarchical S3 schema
- [02-cedar-impl.md](02-cedar-impl.md) - Implementation spec
