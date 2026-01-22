# Additional Gaps Analysis: Manifest-Based Authorization (Post-Implementation Review)

## Executive Summary

This document identifies **NEW GAPS and REMAINING ISSUES** discovered after the initial implementation phase documented in `03-package-gaps.md`. While significant progress has been made (approximately 70-75% complete), several **critical integration gaps** and **new architectural issues** have emerged.

### Key Findings

**Good News:**
- ✅ Manifest resolution fully implemented with quilt3 integration
- ✅ Token type routing complete and well-tested
- ✅ Write operations blocked at multiple levels
- ✅ Control plane API endpoints exist for both RAJ-package and TAJ-package tokens
- ✅ Cedar schema defines Package entity with correct attributes

**Critical Issues:**
1. **Cedar Compiler Doesn't Support Package Resources** - Schema defines Package, but compiler can't compile Package policies
2. **Package Wildcards Not Integrated** - Function exists but never used in policy evaluation
3. **No End-to-End Integration Tests** - Only mocked scenarios tested, no real workflow validation
4. **Manifest Error Handling Gaps** - No tests for quilt3 failures, network issues, corrupted packages
5. **Package Policy Compilation Incomplete** - Cannot write and compile Cedar policies for packages

### Risk Assessment

- **Security**: Medium (write operations blocked correctly, but validation gaps exist)
- **Functionality**: High (core feature partially functional but Cedar integration broken)
- **Production Readiness**: Not Ready (critical compiler gap blocks policy-based authorization)

---

## 1. Critical New Gaps

### 1.1 CRITICAL: Cedar Compiler Doesn't Handle Package Resources

**Discovery:** While the Cedar schema correctly defines the `Package` entity (with `registry`, `packageName`, `hash` attributes) and the `quilt:ReadPackage` action, the Cedar compiler in `src/raja/compiler.py` has NO specialized handling for Package resource types.

**Evidence:** `compiler.py` lines 141-167:

```python
if resource_type == "S3Object":
    # Specialized S3Object handling
elif resource_type == "S3Bucket":
    # Specialized S3Bucket handling
else:
    # Generic fallthrough - just formats as-is
    return [format_scope(resource_type, resource_id, action) for action in actions]
```

**Impact:**

When compiling a Cedar policy like:
```cedar
permit(
    principal == User::"alice",
    action == Action::"quilt:ReadPackage",
    resource == Package::"quilt+s3://my-bucket#package=example/dataset@abc123"
);
```

The compiler produces:
```
Package:quilt+s3://my-bucket#package=example/dataset@abc123:quilt:ReadPackage
```

But package token enforcement expects scopes in a different format (based on quilt_uri claims). This means:
- **Package policies compile but produce incompatible scopes**
- **Token enforcement will never match compiled policy scopes**
- **Result: Package grants fail even when authorized by policy**

**Files Affected:**
- `src/raja/compiler.py` - Missing Package resource handler
- `src/raja/cedar/parser.py` - May need Package-specific parsing

**Tests Missing:**
- No test that compiles a Package policy and verifies scope format
- No test that creates a package token from compiled policy
- No test of end-to-end workflow: policy → compilation → token → enforcement

**Recommendation:** CRITICAL - Must implement specialized Package resource compilation before any production use

**Proposed Fix:**
```python
# In compiler.py
elif resource_type == "Package":
    # Extract quilt_uri from Package entity
    # Parse to get registry, packageName, hash
    # Format scope as expected by package token enforcement
    # Example: "Package:{packageName}@{hash}:quilt:ReadPackage"
```

---

### 1.2 CRITICAL: Package Name Wildcard Function Not Integrated

**Discovery:** The `package_name_matches(pattern, package_name)` function exists in `quilt_uri.py` (lines 91-95) and has unit tests, but is **NEVER CALLED** anywhere in the codebase.

**Evidence:** Grep results show:
- Function defined: `quilt_uri.py:91`
- Unit tests: `test_quilt_uri.py:44-55`
- **Zero usage** in compiler, parser, or enforcement logic

**Impact:**

Cannot write Cedar policies with package name wildcards like:
```cedar
permit(
    principal == User::"data-scientists",
    action == Action::"quilt:ReadPackage",
    resource in Package::"experiment/*"  // This doesn't work
);
```

The wildcard matching function exists but is orphaned - no integration path exists to use it during:
- Policy compilation
- Policy evaluation in AVP
- Token scope matching

**Expected Behavior:**

1. Cedar policy includes `Package::"exp*"` as resource pattern
2. Compiler recognizes wildcard and expands to matching packages
3. OR: AVP evaluates pattern at policy decision time
4. OR: Token enforcement checks pattern match against requested package

**Current Behavior:**

Pattern is treated as literal string, no wildcard expansion or matching occurs.

**Files Affected:**
- `src/raja/compiler.py` - Needs wildcard expansion logic
- `src/raja/cedar/parser.py` - May need pattern extraction
- `src/raja/enforcer.py` - May need pattern matching in scope checks

**Tests Missing:**
- No test compiling policy with package wildcard
- No test issuing token for wildcard-matched package
- No test enforcing access with wildcard scope

**Recommendation:** CRITICAL - Package wildcards are required for practical policy authoring as documented in spec

---

### 1.3 HIGH: No End-to-End Integration Tests

**Discovery:** Integration tests exist (`tests/integration/test_package_map.py`) but only test translation grant enforcement with mocked resolvers. There are NO end-to-end tests covering the complete workflow.

**Missing Test Scenarios:**

1. **Complete Package Grant Flow:**
   - Write Cedar policy for Package resource
   - Compile policy to scopes
   - Request package token via control plane API
   - Enforce package token against real/stubbed manifest
   - Verify S3 access decision

2. **Complete Translation Grant Flow:**
   - Write Cedar policy for Package with logical paths
   - Request translation token via control plane API
   - Enforce translation with real manifest resolver
   - Verify logical-to-physical translation

3. **Token Type Routing Integration:**
   - Issue all three token types (RAJ-path, RAJ-package, TAJ-package)
   - Call unified enforcement endpoint
   - Verify routing dispatches correctly

4. **Real Manifest Resolution:**
   - Use actual quilt3 package (or moto-mocked S3)
   - Resolve manifest to file list
   - Check membership for various S3 objects
   - Verify performance with large packages

**Current Test Gap:**

`test_package_map.py` only tests:
```python
# Mock resolver - not real quilt3
def mock_resolver(uri):
    return {"data/file1.csv": [S3Location(bucket="physical", key="v1/file1.csv")]}

# Tests translation enforcement but not:
# - Token issuance via API
# - Policy compilation
# - Real manifest resolution
```

**Impact:**

Cannot verify:
- Complete feature works as designed
- Performance characteristics
- Error handling in real scenarios
- Integration between components

**Recommendation:** HIGH - Add E2E integration tests before production release

---

### 1.4 MEDIUM: Cedar Schema Missing quilt:WritePackage Action

**Discovery:** The specification says `quilt:WritePackage` should be explicitly rejected with a clear error. Current implementation blocks it at the control plane API level, but the Cedar schema doesn't define the action at all.

**Evidence:**

`policies/schema.cedar` defines only:
```cedar
action "quilt:ReadPackage" appliesTo {
    principal: [User, Role],
    resource: [Package]
}
```

No `quilt:WritePackage` action exists.

**Current Behavior:**

If someone tries to write a Cedar policy with `quilt:WritePackage`:
```cedar
permit(
    principal == User::"alice",
    action == Action::"quilt:WritePackage",  // Not in schema
    resource == Package::"..."
);
```

The policy fails **Cedar schema validation** with a cryptic error about undefined action, not a clear "write packages not supported" message.

**Expected Behavior:**

1. Cedar schema defines `quilt:WritePackage` action
2. Control plane API rejects it with clear message (already implemented)
3. Policy validation can reference the action (for deny rules)
4. Error message clearly states "write operations not supported for packages"

**Files Affected:**
- `policies/schema.cedar` - Add WritePackage action definition

**Impact:** Medium - Affects policy authoring experience and error clarity

**Recommendation:** MEDIUM - Add action to schema with documentation that it's rejected at runtime

---

## 2. Error Handling and Edge Case Gaps

### 2.1 HIGH: Manifest Resolution Error Paths Not Tested

**Discovery:** Manifest resolution implementation exists (`src/raja/manifest.py`) but has minimal error handling tests.

**Tested Scenarios:**
- ✅ Valid package resolution
- ✅ Valid translation mapping
- ✅ Membership checking

**UNTESTED Error Scenarios:**

1. **quilt3 Import Failures:**
   - quilt3 not installed (currently raises RuntimeError)
   - Incompatible quilt3 version
   - Import error due to missing dependencies

2. **Registry Connection Failures:**
   - Invalid registry URL
   - Network timeout
   - Authentication failures
   - SSL/TLS certificate errors

3. **Package Not Found:**
   - Non-existent package
   - Invalid hash reference
   - Package deleted from registry

4. **Corrupted Package Metadata:**
   - Malformed manifest structure
   - Missing required fields
   - Invalid S3 location formats

5. **Performance Edge Cases:**
   - Very large packages (10,000+ files)
   - Deep directory structures
   - Long file paths
   - Large file sizes in manifest

**Evidence:**

`manifest.py` has minimal error handling:
```python
def resolve_package_manifest(quilt_uri: str) -> list[S3Location]:
    try:
        import quilt3 as q3
    except ImportError:
        raise RuntimeError("quilt3 is required for package resolution")

    # Parse URI
    pkg = q3.Package.browse(package_name, registry=registry, top_hash=hash_val)

    # Extract locations - NO ERROR HANDLING for pkg operations
```

**Impact:**

Unknown behavior when:
- Registry is unavailable (likely exception bubbles up)
- Package is corrupted (likely exception bubbles up)
- Large packages exceed memory (likely OOM or timeout)
- Network is slow (likely timeout without retry)

**Recommendation:** HIGH - Add comprehensive error path tests and hardening

**Proposed Tests:**
- Mock quilt3 to raise various exceptions
- Test timeout scenarios with large manifests
- Test malformed package structures
- Test network connectivity issues

---

### 2.2 MEDIUM: Token Claim Validation Gaps

**Discovery:** Token validation has good coverage but some edge cases are missing.

**Missing Tests:**

1. **Empty String Claims:**
   - `quilt_uri = ""` (empty string vs missing)
   - `mode = ""` (empty string vs wrong value)
   - `logical_bucket = ""` in translation tokens

2. **Mode Field Missing Entirely:**
   - Current code: `if mode != "read"` fails when mode is None
   - Error message "token mode must be 'read'" is misleading for missing field
   - Should distinguish "missing mode" from "wrong mode"

3. **Claim Type Validation:**
   - What if `quilt_uri` is an integer instead of string?
   - What if `mode` is a boolean instead of string?
   - What if claims are lists/objects instead of scalars?

**Evidence:**

`token.py` lines 201-203:
```python
mode = payload.get("mode")
if mode != "read":
    raise TokenValidationError("token mode must be 'read'")
```

If `mode` is `None` (missing), error says "must be 'read'" not "mode claim is required"

**Impact:** Medium - Error messages may mislead users about validation failures

**Recommendation:** MEDIUM - Add explicit presence checks and type validation

---

### 2.3 MEDIUM: Manifest Resolver Empty Result Ambiguity

**Discovery:** When `enforce_translation_grant()` receives an empty list from manifest resolver, it's indistinguishable from genuine mapping gaps vs resolver errors.

**Evidence:** `enforcer.py` lines 328-336:

```python
targets = manifest_resolver(payload["quilt_uri"])
if not targets:
    logger.warning("package_map_translation_missing", ...)
    return Decision(allowed=False, reason="logical key not mapped in package")
```

**Scenarios Producing Empty List:**

1. Logical key genuinely not in package (correct denial)
2. Package manifest is empty (corrupt package?)
3. Manifest resolver encountered error and returned `[]` instead of raising exception
4. Resolver timed out and returned empty default

**Current Behavior:** All scenarios produce same decision: "logical key not mapped in package"

**Impact:**

Cannot distinguish between:
- Authorization denial (correct behavior)
- Technical failure (should retry or alert)
- Data corruption (should investigate)

**Recommendation:** MEDIUM - Distinguish resolver failures from authorization denials

**Proposed Fix:**
- Resolver should raise exception on errors, not return `[]`
- Enforce function catches exception and returns technical error decision
- Only return authorization denial when resolver succeeds with empty result

---

## 3. Integration and Architecture Gaps

### 3.1 MEDIUM: Control Plane Doesn't Provide Membership Checker

**Discovery:** Control plane issues package tokens but doesn't provide the `membership_checker` callback required for enforcement.

**Evidence:**

- Package tokens created in `control_plane.py`
- But `enforce_with_routing()` requires passing `membership_checker` at line 191:
  ```python
  def enforce_with_routing(
      token: str,
      resource: str,
      action: str,
      secret: str,
      membership_checker: Callable[[str, str, str], bool] | None = None,
      ...
  )
  ```

- No central integration point that wires token issuance to enforcement

**Impact:**

Users must:
1. Call control plane API to get package token
2. Separately implement or import membership checker
3. Pass both to enforcement function

This creates **tight coupling** between token issuance and enforcement. The control plane "knows" what manifest resolver to use but doesn't expose it to enforcement.

**Expected Architecture:**

Option A: Control plane provides enforcement endpoint that internally uses correct resolver
Option B: Token includes resolver configuration (risky - leaks implementation details)
Option C: Enforcement library has default resolver that matches control plane behavior

**Current Architecture:** Neither - users must manually wire resolvers

**Recommendation:** MEDIUM - Document resolver wiring pattern or provide unified enforcement API

---

### 3.2 LOW: AVP Context Not Validated in Package Requests

**Discovery:** Control plane accepts optional `context` parameter for package authorization but passes it directly to AVP without validation.

**Evidence:** `control_plane.py` lines 156-157:

```python
if context is not None:
    request["context"] = {"contextMap": context}
```

**Security Concern:**

No validation of:
- Context key names (could contain sensitive data)
- Context value types (could be complex objects)
- Context size (could be very large)
- Context structure (AVP expects specific format)

**Impact:** Low - AVP will validate and reject malformed context, but error is less clear

**Recommendation:** LOW - Add context validation for better error messages and security

---

### 3.3 LOW: Logical Path Validation Incomplete

**Discovery:** Translation token requests validate logical path consistency but don't validate S3 naming rules.

**Evidence:** `control_plane.py` lines 73-81:

```python
@model_validator(mode="after")
def _validate_logical(self) -> TranslationTokenRequest:
    # Validates consistency between logical_bucket/logical_key and logical_s3_path
    # But no S3 bucket naming validation
```

**Missing Validation:**

1. S3 bucket naming rules:
   - Must be 3-63 characters
   - Lowercase letters, numbers, hyphens only
   - Cannot start/end with hyphen
   - Cannot contain consecutive dots

2. S3 key format:
   - Cannot start with `/`
   - Cannot contain `//`
   - Cannot contain null bytes

3. Length limits:
   - Bucket name max 63 chars
   - Key max 1024 chars

**Impact:** Low - S3 will reject invalid names, but error is less clear than validation at request time

**Recommendation:** LOW - Add S3 naming validation to request models

---

## 4. Test Coverage Gaps Summary

### Unit Test Gaps

| Component | Missing Tests |
|-----------|---------------|
| Manifest Resolution | quilt3 failures, network errors, corrupted packages, large packages |
| Token Validation | Empty string claims, type mismatches, missing required fields |
| Cedar Compiler | Package resource compilation, wildcard expansion |
| Control Plane | AVP failures, malformed requests, context validation |

### Integration Test Gaps

| Workflow | Missing Tests |
|----------|---------------|
| Package Grant E2E | Policy → Compilation → Token → Enforcement |
| Translation Grant E2E | Policy → Token → Translation → Enforcement |
| Token Type Routing | All three token types in one test suite |
| Real Manifest Resolution | Actual quilt3 integration or moto-mocked S3 |
| Performance | Large packages, caching, memory usage |

### Property-Based Test Gaps

| Property | Missing Tests |
|----------|---------------|
| Package URI Parsing | Fuzz testing with random URIs |
| Wildcard Matching | Property: pattern match = fnmatch |
| Token Roundtrip | Property: encode(decode(token)) = token |
| Enforcement Determinism | Property: same request = same decision |

---

## 5. Prioritized Recommendations

### Must Fix Before Production (CRITICAL)

1. **Implement Cedar Compiler Package Support** ⚠️ BLOCKS POLICY-BASED AUTHORIZATION
   - File: `src/raja/compiler.py`
   - Add specialized handling for Package resource type
   - Extract quilt_uri components and format scopes correctly
   - Test policy compilation produces enforceable scopes
   - Estimated effort: 1-2 days

2. **Integrate Package Name Wildcard Matching** ⚠️ BLOCKS PRACTICAL POLICY AUTHORING
   - Files: `src/raja/compiler.py`, `src/raja/enforcer.py`
   - Use `package_name_matches()` during policy compilation or enforcement
   - Test wildcards in Cedar policies
   - Test scope matching with wildcard patterns
   - Estimated effort: 2-3 days

3. **Add End-to-End Integration Tests** ⚠️ BLOCKS PRODUCTION CONFIDENCE
   - File: `tests/integration/test_package_grants_e2e.py` (NEW)
   - Test complete flow: policy → token → enforcement
   - Use real or moto-mocked quilt3 package
   - Verify all three token types
   - Estimated effort: 3-5 days

### Should Fix (HIGH Priority)

4. **Comprehensive Manifest Error Handling Tests**
   - Files: `tests/unit/test_manifest.py`, `tests/integration/`
   - Test quilt3 failures (import, connection, auth)
   - Test package not found scenarios
   - Test corrupted package metadata
   - Test network timeouts and retries
   - Estimated effort: 2-3 days

5. **Token Claim Validation Hardening**
   - Files: `src/raja/token.py`, `tests/unit/test_token.py`
   - Add explicit presence checks for required claims
   - Validate claim types (string vs int vs list)
   - Improve error messages for missing vs invalid claims
   - Test empty string claims
   - Estimated effort: 1-2 days

6. **Manifest Resolver Error vs Empty Distinction**
   - Files: `src/raja/enforcer.py`, `src/raja/manifest.py`
   - Resolver raises exception on errors (not empty list)
   - Enforcement catches exception and returns technical error
   - Update tests to verify error handling
   - Estimated effort: 1 day

7. **Add quilt:WritePackage to Cedar Schema**
   - File: `policies/schema.cedar`
   - Define WritePackage action in schema
   - Document that it's rejected at runtime
   - Update tests to verify policy validation
   - Estimated effort: 1 day

### Nice to Have (MEDIUM/LOW Priority)

8. **Control Plane Enforcement Integration**
   - Provide unified endpoint that handles token + enforcement
   - Or document resolver wiring pattern clearly
   - Estimated effort: 2-3 days

9. **AVP Context Validation**
   - Add validation for context structure and size
   - Better error messages for malformed context
   - Estimated effort: 1 day

10. **S3 Naming Validation**
    - Validate bucket names and key formats in token requests
    - Better error messages than S3 errors
    - Estimated effort: 1 day

11. **Property-Based Tests**
    - Add hypothesis tests for URI parsing, wildcard matching
    - Test enforcement determinism properties
    - Estimated effort: 2-3 days

---

## 6. Estimated Timeline to Production Readiness

### Critical Path (Blocking Issues)

- **Week 1**: Cedar compiler Package support (2 days) + wildcard integration (3 days)
- **Week 2**: End-to-end integration tests (5 days)

**Total: 2 weeks for minimum viable production release**

### Full Production Readiness (All HIGH Priority Items)

- **Week 3**: Error handling tests (3 days) + token validation hardening (2 days)
- **Week 4**: Resolver error distinction (1 day) + Cedar schema update (1 day) + buffer (3 days)

**Total: 4 weeks for production-ready with high confidence**

### Complete Hardening (Including MEDIUM/LOW)

- **Week 5**: Integration improvements (3 days) + property tests (2 days)
- **Week 6**: Validation improvements (2 days) + documentation (3 days)

**Total: 6 weeks for fully hardened production release**

---

## 7. Conclusion

The manifest-based authorization feature has made **substantial progress** since the initial gap analysis:

✅ **Completed:**
- Manifest resolution with quilt3 integration
- Token type routing logic
- Write operation blocking at multiple levels
- Control plane API endpoints for both token types
- Cedar schema with Package entity

❌ **Critical Remaining Gaps:**
- Cedar compiler cannot compile Package policies (BLOCKS policy-based authorization)
- Package wildcards not integrated (BLOCKS practical policy authoring)
- No end-to-end integration tests (BLOCKS production confidence)
- Manifest error handling not tested (RISK in production)

**Recommendation:** Focus on **critical path items first** (Cedar compiler + wildcards + E2E tests) for a 2-week minimum viable release, then address high-priority items for a 4-week production-ready release.

The feature is approximately **70-75% complete** and requires **2-6 weeks** depending on desired confidence level for production deployment.

---

## Appendix A: Files Requiring Changes

### Critical Changes (MUST FIX)

- `src/raja/compiler.py` - Add Package resource compilation
- `src/raja/cedar/parser.py` - Extract package patterns from policies
- `src/raja/enforcer.py` - Integrate wildcard matching in scope checks
- `tests/integration/test_package_grants_e2e.py` - NEW FILE - E2E tests
- `tests/unit/test_compiler.py` - Add Package compilation tests

### High Priority Changes (SHOULD FIX)

- `tests/unit/test_manifest.py` - Add error scenario tests
- `tests/integration/test_manifest_real.py` - NEW FILE - Real quilt3 tests
- `src/raja/token.py` - Improve claim validation
- `tests/unit/test_token.py` - Add edge case tests
- `src/raja/enforcer.py` - Distinguish resolver errors from empty results
- `policies/schema.cedar` - Add WritePackage action definition

### Medium/Low Priority Changes (NICE TO HAVE)

- `src/raja/server/routers/control_plane.py` - Add context validation
- `src/raja/models.py` - Add S3 naming validation to request models
- `tests/hypothesis/test_properties.py` - NEW FILE - Property-based tests
- Documentation updates for resolver wiring pattern

---

## Appendix B: Key Architectural Decisions Needed

### Decision 1: How Should Package Policies Compile to Scopes?

**Options:**

A. Scope format: `Package:{packageName}@{hash}:quilt:ReadPackage`
   - Pro: Matches package token structure
   - Con: Doesn't include registry information

B. Scope format: `Package:{registry}/{packageName}@{hash}:quilt:ReadPackage`
   - Pro: Fully qualified package reference
   - Con: More complex parsing

C. Scope format: Keep full quilt URI as resource ID
   - Pro: Preserves all information
   - Con: Very long scope strings

**Recommendation:** Option B - Fully qualified but structured

### Decision 2: When Should Package Wildcards Be Evaluated?

**Options:**

A. At policy compilation time (expand wildcards to all matching packages)
   - Pro: No runtime pattern matching
   - Con: Must recompile when new packages added

B. At token issuance time (expand wildcards to packages user can access)
   - Pro: Dynamic package list
   - Con: Potentially large token size

C. At enforcement time (check if requested package matches wildcard pattern)
   - Pro: Most flexible
   - Con: Requires pattern matching on every request

**Recommendation:** Option C - Enforcement-time matching (most flexible, matches spec)

### Decision 3: How Should Resolvers Be Provided to Enforcement?

**Options:**

A. Control plane provides unified enforcement endpoint
   - Pro: Simple for users
   - Con: Couples control plane and enforcement

B. Token includes resolver configuration (registry URL, etc.)
   - Pro: Self-contained token
   - Con: Leaks implementation details, security risk

C. Enforcement library has default resolver matching control plane
   - Pro: Works out of box
   - Con: Tight coupling between components

D. Users explicitly wire resolvers (current implementation)
   - Pro: Flexible, explicit
   - Con: More complex for users

**Recommendation:** Option A or C - Provide default behavior with override capability
