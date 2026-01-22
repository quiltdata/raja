# Gap Analysis: Manifest-Based Authorization (REVISED)

## Executive Summary

The manifest-based authorization feature (Package Grants and Translation Access Grants) has a **solid foundation** with core token and enforcement logic implemented. However, there are **critical gaps** that prevent production deployment:

### Top 5 Critical Issues

1. **MISSING: Package Manifest Resolution** - No implementation to resolve `quilt_uri` to actual file lists (core enforcement requirement)
2. **MISSING: Cedar Schema for Package Entity** - Package resource type not defined in Cedar schema with correct attributes
3. **MISSING: Control Plane Integration** - No API endpoints for requesting RAJ-package/TAJ-package tokens
4. **MISSING: Token Type Routing** - No logic to route between RAJ-path, RAJ-package, and TAJ-package enforcement
5. **MISSING: Package Name Wildcard Support** - Cannot write policies with patterns like `"exp*"` or `"experiment/*"`

### Risk Assessment

- **Security**: Medium-High (fail-closed semantics appear correct, but untested error paths are concerning)
- **Functionality**: High (core feature incomplete - no manifest resolution, no routing, no wildcards)
- **Production Readiness**: Not Ready (critical components missing)

---

## 1. Functional Gaps

### 1.1 CRITICAL: Package Manifest Resolution (Missing)

**Specification Says:** (01-package-grant.md, lines 372-451)

- RAJEE must resolve `quilt_uri` to list of `(bucket, key)` tuples
- Two options suggested: Lambda Authorizer or Pre-compiled Cache
- Recommendation: Start with Lambda Authorizer using quilt3

**Implementation Reality:**

- `enforce_package_grant()` accepts a `membership_checker` callback (enforcer.py:187-243)
- `enforce_translation_grant()` accepts a `manifest_resolver` callback (enforcer.py:246-308)
- **NO ACTUAL IMPLEMENTATION** of these callbacks anywhere in the codebase
- No quilt3 integration
- No package fetching logic
- No S3 manifest reading

**Impact:** Package grants **cannot function** without this. The enforcement logic exists but has no way to determine package membership.

**Files Affected:**

- MISSING: `lambda_handlers/package_resolver/` (or similar)
- MISSING: Manifest resolution logic in authorizer

**Recommendation:** CRITICAL - Must implement before any production use

### 1.2 CRITICAL: Cedar Schema Extension (Missing)

**User Requirements:**

- Define `Package` entity type in Cedar schema
- Define `quilt:ReadPackage` action only (WritePackage deferred - must ERROR if requested)
- Package entity should have `registry`, `packageName`, `hash` attributes (NOT `uri`)

**Implementation Reality:**

- `policies/schema.cedar` defines only S3Object/S3Bucket entities
- No Package entity defined
- No quilt:ReadPackage action
- Cannot write Cedar policies for package grants

**Impact:** Cannot use AVP to make authorization decisions for packages. The control plane cannot compile package policies.

**Recommendation:** CRITICAL - Blocks policy-based authorization

### 1.3 HIGH: Control Plane Token Issuance API (Missing)

**User Requirements:**

- POST /token endpoint should accept package authorization requests for **both RAJ-package and TAJ-package** tokens
- Request should include principal, resource (Package URI), action (quilt:ReadPackage)
- Response should return JWT with appropriate claims:
  - **RAJ-package**: `quilt_uri` + `mode` claims
  - **TAJ-package**: `quilt_uri` + `mode` + `logical_bucket` + `logical_key` claims

**Implementation Reality:**

- Token creation functions exist: `create_token_with_package_grant()`, `create_token_with_package_map()`
- No API endpoint to invoke these
- No integration with AVP to make ALLOW/DENY decisions
- Control plane handler is just a Mangum wrapper with no package-specific routes

**Impact:** Cannot request package tokens through API. Tokens can only be created programmatically in tests.

**Recommendation:** HIGH - Needed for end-to-end workflow

### 1.4 CRITICAL: Token Type Routing (Missing)

**User Requirements:**

- Three distinct token types must coexist:
  - **RAJ-path**: Path grants with `scopes` claim
  - **RAJ-package**: Package grants with `quilt_uri` + `mode` claims
  - **TAJ-package**: Translation grants with `quilt_uri` + `mode` + `logical_bucket/logical_key` claims
- RAJEE must route to correct enforcement function based on token type
- Token types are **mutually exclusive** (NOT mixed in a single token)

**Implementation Reality:**

- `enforce()` handles RAJ-path (scopes)
- `enforce_package_grant()` handles RAJ-package (quilt_uri)
- `enforce_translation_grant()` handles TAJ-package
- No unified routing logic that dispatches to correct enforcement function
- No tests for token type detection or routing logic

**Impact:** RAJEE cannot determine which enforcement path to use. Feature is non-functional without routing.

**Recommendation:** CRITICAL - Implement token type routing before production

### 1.5 CRITICAL: Package Name Wildcard Support (Missing)

**User Requirements:**

- Must support package name wildcards in Cedar policies
- Examples: `"exp*"`, `"experiment/*"`, `"experiment/02*"`
- Access is **ONLY granted to a single revision** (hash required in quilt_uri)
- Wildcard applies to package name matching at policy evaluation time, NOT version matching

**Implementation Reality:**

- QuiltUri parsing enforces hash requirement (quilt_uri.py:26 - correct for immutability)
- No support for package name wildcards in Cedar policies
- No pattern matching logic for package names

**Impact:** Cannot write policies like "grant access to all experiment packages" - must enumerate every package individually. Severely limits usability.

**Recommendation:** CRITICAL - Required for practical policy authoring

### 1.6 HIGH: Write Operations for Packages (Must Block)

**User Requirements:**

- Write operations for packages are **NOT IMPLEMENTED**
- Must **ERROR** if `quilt:WritePackage` action is requested
- Only read-only access (`quilt:ReadPackage`) is supported

**Implementation Reality:**

- Token mode supports "readwrite" (incorrect - should only support "read")
- S3 PutObject operations are allowed with readwrite mode (must be blocked)
- No validation that write operations are rejected

**Impact:** Could allow write operations on immutable packages, violating core design principle.

**Recommendation:** HIGH - Must explicitly block write operations and return clear error

---

## 2. Test Coverage Gaps

### 2.1 CRITICAL: No End-to-End Integration Tests

**What's Missing:**

- No test that goes: Cedar policy → Token issuance → Manifest resolution → Enforcement → S3 access
- Integration test in `test_package_map.py` uses mock manifest resolver (line 28-34)
- No real manifest fetching from S3 or quilt3

**Impact:** Cannot verify the feature works as a complete system

**Recommendation:** CRITICAL - Add E2E tests with real or stubbed manifest storage

### 2.2 HIGH: Error Path Coverage for Package Tokens

**Tested Scenarios:**

- Valid package token (test_enforcer.py:220-233)
- Non-member denial (test_enforcer.py:236-249)
- Write with read mode denial (test_enforcer.py:252-265)

**UNTESTED Scenarios:**

- Expired package token
- Malformed quilt_uri in token
- Invalid signature on package token
- Token with missing quilt_uri claim
- Token with empty quilt_uri
- Token with mutable URI (no hash)
- Membership checker throws exception
- Membership checker returns non-boolean
- Invalid S3 bucket name in PackageAccessRequest
- Empty key in PackageAccessRequest
- Null values in token claims

**Impact:** Unknown behavior in error conditions. May fail open or leak error details.

**Recommendation:** HIGH - Add comprehensive error path tests

### 2.3 HIGH: Error Path Coverage for Translation Grants

**Tested Scenarios:**

- Valid translation (test_enforcer.py:268-297)
- Bucket mismatch denial (test_enforcer.py:300-321)
- Unmapped key denial (test_enforcer.py:324-345)

**UNTESTED Scenarios:**

- Manifest resolver throws exception
- Manifest resolver returns None
- Manifest resolver returns invalid data
- logical_s3_path parsing failures
- Conflicting logical_bucket/logical_key vs logical_s3_path
- Translation returns empty list (currently treated as deny, but not tested)
- Translation returns multiple targets (spec mentions "small set of targets" but not tested)
- Expired translation token
- Write operations with translation grants

**Impact:** Translation grant enforcement may fail incorrectly or leak error information

**Recommendation:** HIGH - Add error scenario tests

---

## Prioritized Recommendations

### Must Fix Before Production (CRITICAL)

1. **Implement Package Manifest Resolution**
   - Files: Create `lambda_handlers/package_resolver/` or integrate into authorizer
   - Integrate quilt3 or implement S3 manifest reading
   - Implement membership_checker and manifest_resolver callbacks
   - Add caching layer for large packages

2. **Extend Cedar Schema**
   - Files: `policies/schema.cedar`
   - Add Package entity with `registry`, `packageName`, `hash` attributes
   - Add `quilt:ReadPackage` action
   - Test Cedar policy compilation for packages

3. **Implement Token Type Routing**
   - Files: `src/raja/enforcer.py`
   - Create router function that inspects token claims and dispatches to:
     - `enforce()` for RAJ-path (scopes claim)
     - `enforce_package_grant()` for RAJ-package (quilt_uri claim, no logical_* claims)
     - `enforce_translation_grant()` for TAJ-package (quilt_uri + logical_bucket/logical_key claims)
   - Add tests for routing logic

4. **Add Package Name Wildcard Support**
   - Files: Cedar policy evaluation or token issuance logic
   - Implement pattern matching for package names (`*` and `/` patterns)
   - Add tests for wildcard matching

5. **Block Write Operations**
   - Files: `src/raja/token.py`, `src/raja/enforcer.py`
   - Remove "readwrite" mode support for package tokens (only "read" allowed)
   - Add validation to reject `quilt:WritePackage` action requests
   - Return clear error when write operations attempted

6. **Add Control Plane API Endpoints**
   - Files: Extend raja.server.app with package token routes
   - POST /token/package - issue RAJ-package token
   - POST /token/translation - issue TAJ-package token
   - Integrate with AVP for authorization decisions

7. **Comprehensive Error Path Testing**
   - Files: `tests/unit/test_enforcer.py`, `test_token.py`
   - Test all failure modes (expired tokens, malformed URIs, exceptions)
   - Verify fail-closed behavior in all error paths

### Should Fix (HIGH Priority)

8. **End-to-End Integration Tests**
   - Files: `tests/integration/test_package_grants.py` (new)
   - Test complete flow from policy to enforcement
   - Test with real or stubbed manifest storage
   - Validate performance characteristics

---

## Deferred Items (Separate Document Recommended)

The following items are deferred to a future phase or separate document focused on hardening and optimization:

### Security Hardening (Deferred)

- QuiltUri Validation Edge Cases (path traversal, injection attacks, length limits)
- Token Claim Validation Completeness (registry whitelisting, stricter validation)
- Error Information Leakage (review error message verbosity)

### Performance & Scale (Deferred)

- Large Package Handling (10,000+ files performance testing)
- Manifest Resolution Caching Strategy (Redis/DynamoDB implementation)
- PackageMap Translation Edge Cases (multiple targets, cross-bucket)
- Performance and Scale Testing (benchmark tests)

### Operational (Deferred)

- Monitoring and Observability (metrics, tracing, alerting)
- Error Alerting or Debugging Tools (CLI tools, admin interface)
- Deployment or Rollback Guidance (feature flags, migration strategy)

**Recommendation:** Create `04-package-hardening.md` for these deferred items once core functionality is complete.

---

## Conclusion

The manifest-based authorization feature has a **well-designed foundation** with clear specifications and correct fail-closed semantics in the enforcement logic. However, it is **not production-ready** due to missing critical components:

- No manifest resolution implementation (blocks all functionality)
- No Cedar schema extension with correct attributes (blocks policy-based authorization)
- No token type routing (blocks correct enforcement dispatch)
- No package name wildcard support (blocks usable policies)
- No control plane integration (blocks token issuance)
- Write operations not blocked (violates immutability)

**Estimated Completion:** 2-3 weeks for critical items (items 1-7 above)

**Recommended Next Steps:**

1. Implement manifest resolution with quilt3 integration
2. Extend Cedar schema with correct Package entity (`registry`, `packageName`, `hash`)
3. Implement token type routing logic in RAJEE
4. Add package name wildcard support in Cedar policies
5. Block write operations explicitly
6. Add control plane API endpoints for RAJ-package and TAJ-package token issuance
7. Comprehensive error path testing

---

## Critical Files for Implementation

Based on this gap analysis, here are the most critical files for completing the manifest-based authorization feature:

- `lambda_handlers/package_resolver/handler.py` - **NEW FILE** - Core manifest resolution logic with quilt3 integration
- `policies/schema.cedar` - Extend with Package entity (`registry`, `packageName`, `hash` attributes) and `quilt:ReadPackage` action
- `src/raja/enforcer.py` - Add token type routing logic and block write operations
- `src/raja/server/app.py` - Add POST /token/package and /token/translation API endpoints
- `src/raja/token.py` - Remove "readwrite" mode for package tokens, add validation
- `tests/integration/test_package_grants_e2e.py` - **NEW FILE** - End-to-end integration tests with manifest resolution
