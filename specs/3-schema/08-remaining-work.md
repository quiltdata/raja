# Remaining Implementation Work

This document tracks all unimplemented or unvalidated work items identified through the failure mode testing framework and [06-failure-fixes.md](06-failure-fixes.md).

**Status as of:** 2026-01-20

## Overview

- **Total Test Runners:** 40
- **Functional/Passing:** 17 (42.5%)
- **Not Implemented:** 23 (57.5%)

The admin UI failure testing framework now provides comprehensive visibility into RAJA's authorization security posture, with all 40 test runners implemented. However, 23 tests require additional infrastructure or fixes before they can run functionally.

---

## 1. Cedar Policy Compilation (CRITICAL Priority)

### 1.1 Replace Custom Cedar Parser/Compiler

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 2.1: Forbid policies
- 2.2: Policy syntax errors
- 2.3: Conflicting policies
- 2.4: Wildcard expansion
- 2.5: Template variables
- 2.6: Principal-action mismatch
- 2.7: Schema validation

**Work Required:**

- Remove regex-based Cedar parser (`src/raja/cedar/parser.py`)
- Integrate official Cedar Rust tooling via:
  - Option A: Cedar CLI subprocess calls
  - Option B: PyO3 Python bindings to Cedar Rust library
  - Option C: cedar-wasm WebAssembly module
- Implement Cedar schema validation
- Add forbid policy support to compiler
- Support Cedar policy templates with variable substitution
- Add wildcard pattern expansion in resource matching

**Validation:**

- All 7 Cedar compilation tests (2.1-2.7) should pass
- `principal in`, `action in`, and complex `when` clauses parse successfully
- Forbid policies are correctly compiled and enforced
- Template handling follows Cedar semantics

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 1

---

## 2. Scope Enforcement Enhancements (HIGH Priority)

### 2.1 Wildcard Scope Support

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 3.5: Wildcard boundaries
- 3.8: Malformed scope format (partial)

**Work Required:**

- Implement wildcard matching in scope enforcement
- Ensure wildcards respect component boundaries (e.g., `bucket:*` doesn't match `bucket-admin`)
- Add scope string validation (reject malformed formats)
- Update harness to support scope arrays (not just single s3 claim)

**Validation:**

- Test 3.5 passes with wildcard boundary checking
- Wildcards match within intended boundaries only
- Invalid scope strings are safely rejected

### 2.2 Multi-Scope Enforcement

**Status:** NOT IMPLEMENTED (Harness Limitation)

**Blocking Tests:**

- 3.6: Scope ordering
- 3.8: Malformed scope format

**Work Required:**

- Extend harness to support multiple scopes in token claims
- Test that scope evaluation order doesn't affect authorization decisions
- Validate raw scope string parsing (not just structured s3 claims)

**Validation:**

- Same request with scopes in different orders yields consistent results
- Malformed scope strings are detected and rejected

---

## 3. Cross-Component Integration (CRITICAL Priority)

### 3.1 Schema-Policy Consistency Validation

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 5.3: Schema-policy consistency

**Work Required:**

- Cross-validate AVP schema entities with enforcer expectations
- Ensure resource types in Cedar schema match enforcement logic
- Add automated checks for schema drift

**Validation:**

- Schema entities referenced in policies exist and match enforcer types
- Automated validation catches schema-policy mismatches

### 3.2 DynamoDB Eventual Consistency Handling

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 5.4: DynamoDB lag

**Work Required:**

- Test rapid policy update → token issuance → enforcement flow
- Verify no authorization gaps due to replication lag
- Consider using strongly consistent reads for critical paths
- Document consistency model and implications

**Validation:**

- Policy updates immediately reflected in token issuance
- No window where old scopes grant unintended access
- Test passes with rapid update/issuance cycles

### 3.3 Policy Version Tracking

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 5.6: Policy ID tracking

**Work Required:**

- Implement policy versioning API
- Track version increments on policy updates
- Expose version metadata via API
- Consider adding version info to tokens for audit trail

**Validation:**

- Policy version increments on each update
- Version history is queryable
- Tokens can be traced to policy version

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 5

---

## 4. Token Revocation (MEDIUM Priority)

### 4.1 Implement Revocation Mechanism

**Status:** NOT IMPLEMENTED (Design Decision Pending)

**Blocking Tests:**

- 1.6: Token revocation

**Work Required:**

- **Option A:** Implement token revocation with DynamoDB blacklist
- **Option B:** Implement token revocation with Redis cache
- **Option C:** Document that revocation is intentionally not supported
  - Update test to assert "not supported"
  - Document alternative: short TTL + policy-based access control

**Validation:**

- Revoked tokens are rejected on subsequent use
- Revocation propagates across all enforcement points
- Performance impact is acceptable

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 4

---

## 5. Request Parsing (MEDIUM Priority)

### 5.1 URL Encoding Edge Cases

**Status:** NOT IMPLEMENTED (Envoy Layer)

**Blocking Tests:**

- 4.4: URL encoding edge cases

**Work Required:**

- Test double-encoding and unusual URL encodings
- Verify correct decoding in Envoy S3 request parsing
- Ensure no bypass via encoding tricks

**Validation:**

- Double-encoded keys are normalized correctly
- Unusual encodings don't bypass authorization
- Test passes with various encoding edge cases

---

## 6. Operational Features (MEDIUM/LOW Priority)

### 6.1 JWT Secret Rotation

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 6.1: Secrets rotation

**Work Required:**

- Implement multi-key JWKS support
- Add secret rotation mechanism with overlap period
- Test active tokens survive rotation
- Document rotation procedure

**Validation:**

- Secret rotation doesn't break active tokens
- Overlap period allows graceful transition
- Old secrets eventually expire

### 6.2 Rate Limiting

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 6.3: Rate limiting

**Work Required:**

- Add rate limiting middleware (per-IP or per-principal)
- Configure appropriate limits
- Return 429 when rate exceeded

**Validation:**

- Burst of requests triggers rate limiting
- Test 6.3 passes with rate limit enforcement

### 6.3 Policy Store Unavailability Handling

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 6.5: Policy store unavailability

**Work Required:**

- Test fail-closed behavior when AVP is unreachable
- Mock AVP service disruption
- Verify authorization defaults to DENY

**Validation:**

- Authorization requests fail closed when AVP unavailable
- Clear error messages logged
- System recovers when AVP restored

### 6.4 Metrics and Observability

**Status:** NOT IMPLEMENTED

**Blocking Tests:**

- 6.7: Metrics collection

**Work Required:**

- Integrate with CloudWatch or similar
- Record authorization decision metrics
- Track ALLOW/DENY rates, latency, errors
- Add dashboards for monitoring

**Validation:**

- Authorization decisions appear in metrics
- Metrics reflect actual authorization activity
- Dashboards provide useful visibility

---

## 7. Validation of Existing Fixes

These items from [06-failure-fixes.md](06-failure-fixes.md) have integration tests but need verification:

### 7.1 Scope Parsing Validation

**Status:** NEEDS VALIDATION

**Work Required:**

- Verify `tests/unit/test_scope.py::test_parse_scope_rejects_colon_in_resource_id` exists and passes
- Verify `tests/unit/test_token.py::test_validate_token_rejects_non_list_scopes` exists and passes

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 2

### 7.2 Envoy JWT Filter Validation

**Status:** VALIDATED ✅

**Tests Passing:**

- `test_envoy_rejects_expired_token` (integration test)
- `test_envoy_rejects_missing_subject` (integration test)
- `test_envoy_rejects_wrong_audience` (integration test)
- `test_envoy_denies_null_scopes` (integration test)

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 3

### 7.3 Cross-Component Traceability

**Status:** VALIDATED ✅

**Tests Passing:**

- `test_policy_to_token_traceability` (integration test)
- `test_policy_update_invalidates_existing_token` (integration test)

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 5

### 7.4 AVP Policy Store Consistency

**Status:** NEEDS INVESTIGATION

**Work Required:**

- Investigate why `test_avp_policy_store_matches_local_files` may be failing
- Fix template expansion normalization
- Ensure local and remote policies match after expansion

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 6

### 7.5 Error Response Format

**Status:** VALIDATED ✅

**Tests Passing:**

- `test_error_response_format_is_s3_compatible` (integration test)
- `test_health_check_verifies_dependencies` (integration test)

**Reference:** [06-failure-fixes.md](06-failure-fixes.md) Section 7

---

## Test Implementation Summary

### Functional Tests (17 tests passing)

**Token Security (6/6):**

- ✅ 1.1: Expired token
- ✅ 1.2: Invalid signature
- ✅ 1.3: Malformed JWT
- ✅ 1.4: Missing/empty scopes
- ✅ 1.5: Token claim validation
- 🔶 1.6: Token revocation (NOT_IMPLEMENTED, design pending)

**Scope Enforcement (5/8):**

- ✅ 3.1: Prefix attacks (CRITICAL security test)
- ✅ 3.2: Substring attacks (CRITICAL security test)
- ✅ 3.3: Case sensitivity
- ✅ 3.4: Action specificity
- ✅ 3.7: Empty scope handling

**Request Parsing (2/5):**

- ✅ 4.1: Missing authorization header (via integration tests)
- ✅ 4.2: Malformed S3 requests
- ✅ 4.3: Path traversal (CRITICAL security test)
- ✅ 4.5: HTTP method mapping

**Cross-Component (2/6):**

- ✅ 5.1: Compiler-enforcer sync (via integration tests)
- ✅ 5.2: Token-scope consistency (via integration tests)
- ✅ 5.5: JWT claims structure

**Operational (2/7):**

- ✅ 6.2: Clock skew tolerance
- ✅ 6.4: Large token payloads
- ✅ 6.6: Logging sensitive data (via code inspection)

### Not Implemented (23 tests)

**Cedar Compilation (7/7):**

- 🔶 2.1-2.7: All require Cedar Rust tooling integration

**Scope Enforcement (3/8):**

- 🔶 3.5: Wildcard boundaries
- 🔶 3.6: Scope ordering
- 🔶 3.8: Malformed scope format

**Request Parsing (2/5):**

- 🔶 4.4: URL encoding edge cases

**Cross-Component (4/6):**

- 🔶 5.3: Schema-policy consistency
- 🔶 5.4: DynamoDB lag
- 🔶 5.6: Policy ID tracking

**Operational (5/7):**

- 🔶 6.1: Secrets rotation
- 🔶 6.3: Rate limiting
- 🔶 6.5: Policy store unavailability
- 🔶 6.7: Metrics collection

---

## Priority Roadmap

### Phase 1: Critical Security & Correctness (P0)

1. Cedar Rust tooling integration (2.1-2.7)
2. Schema-policy consistency validation (5.3)
3. DynamoDB consistency handling (5.4)
4. Scope validation fixes (per 06-failure-fixes.md Section 2)
5. AVP policy store consistency (per 06-failure-fixes.md Section 6)

### Phase 2: Enhanced Enforcement (P1)

1. Wildcard scope support (3.5)
2. Multi-scope enforcement (3.6, 3.8)
3. Policy version tracking (5.6)
4. URL encoding edge cases (4.4)

### Phase 3: Operational Maturity (P2)

1. Token revocation (1.6) - **or** document as not supported
2. JWT secret rotation (6.1)
3. Rate limiting (6.3)
4. Metrics and observability (6.7)
5. Policy store unavailability handling (6.5)

---

## Notes

- All 40 test runners are implemented in [src/raja/server/routers/failure_tests.py](../../src/raja/server/routers/failure_tests.py)
- Tests marked NOT_IMPLEMENTED include detailed notes about blockers
- Admin UI provides real-time visibility into test status
- This document should be updated as work progresses
