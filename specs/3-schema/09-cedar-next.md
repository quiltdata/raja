# Cedar Policy Compiler: Next Steps

**Status:** Planning
**Priority:** CRITICAL (P0)
**Created:** 2026-01-20

## Overview

RAJA's current Cedar policy compiler uses a custom regex-based parser ([src/raja/cedar/parser.py](../../src/raja/cedar/parser.py)) that provides basic policy parsing but lacks critical features required for production authorization systems. This document outlines the work required to replace the custom parser with official Cedar tooling.

**Current State:**

- ✅ Basic permit policy parsing with regex
- ✅ Simple resource/action/principal extraction
- ✅ Template placeholder recognition (bucket IDs only)
- ⚠️ Forbid effect recognized but not enforced in compilation
- ❌ No schema validation
- ❌ No policy conflict detection
- ❌ No advanced Cedar features (when clauses, context, etc.)

**Blocked Tests:** 7 out of 40 failure mode tests (17.5%)

- 2.1: Forbid policies
- 2.2: Policy syntax errors (partially implemented)
- 2.3: Conflicting policies
- 2.4: Wildcard expansion
- 2.5: Template variables
- 2.6: Principal-action mismatch
- 2.7: Schema validation

---

## Problem Statement

### Current Limitations

The regex-based parser in `src/raja/cedar/parser.py` has fundamental limitations:

1. **No Forbid Enforcement**: Parser recognizes `forbid` keyword but compiler doesn't handle denial policies
2. **Limited Syntax Validation**: May accept invalid Cedar that would fail in official tooling
3. **No Schema Awareness**: Cannot validate entity references against schema
4. **Template Restrictions**: Only supports `{{bucket}}` placeholders in resource IDs
5. **No Advanced Features**: Cannot parse `when`/`unless` conditions, context variables, or complex expressions

### Why This Matters

**Security Impact:**

- Forbid policies are critical for deny-by-default security models
- Invalid policies may silently fail or grant unintended access
- Schema mismatches between policy store and enforcement logic create vulnerabilities

**Correctness Impact:**

- Cannot validate policies before deployment
- No confidence that policies compile correctly
- Difficult to debug policy issues

**Maintainability Impact:**

- Custom parser is a maintenance burden
- Diverges from Cedar specification over time
- Duplicates work done by Cedar team

---

## Proposed Solution

### Replace Custom Parser with Cedar Rust Tooling

Three integration options, in order of preference:

#### Option A: Cedar CLI Subprocess (Recommended)

**Approach:** Shell out to `cedar` CLI for parsing and validation.

**Pros:**

- Minimal dependencies (just Cedar CLI binary)
- Always up-to-date with latest Cedar features
- Mature, battle-tested tooling
- Easy to install and upgrade

**Cons:**

- Subprocess overhead (acceptable for compile-time operation)
- Requires Cedar CLI in deployment environment

**Implementation:**

```python
import subprocess
import json

def parse_policy_with_cedar(policy_str: str, schema_path: str) -> dict:
    """Parse Cedar policy using official CLI."""
    result = subprocess.run(
        ["cedar", "validate", "--schema", schema_path, "--policy", "-"],
        input=policy_str,
        capture_output=True,
        text=True,
        check=True
    )
    return json.loads(result.stdout)
```

**Installation:**

```bash
# Via Cargo
cargo install cedar-policy-cli

# Via Homebrew (macOS)
brew install cedar-policy-cli

# In Docker
RUN cargo install cedar-policy-cli
```

#### Option B: PyO3 Python Bindings

**Approach:** Create Python bindings to Cedar Rust library using PyO3.

**Pros:**

- No subprocess overhead
- Type-safe Rust integration
- Can expose low-level Cedar APIs

**Cons:**

- Requires building native extension
- More complex build/deployment
- Need to maintain bindings as Cedar evolves
- Platform-specific binary compilation

**Implementation Sketch:**

```rust
// cedar_bindings.rs
use pyo3::prelude::*;
use cedar_policy::{Policy, PolicySet, Schema};

#[pyfunction]
fn parse_policy(policy_str: String) -> PyResult<String> {
    let policy = Policy::parse(None, policy_str.as_str())
        .map_err(|e| PyErr::new::<pyo3::exceptions::PyValueError, _>(e.to_string()))?;
    Ok(serde_json::to_string(&policy).unwrap())
}

#[pymodule]
fn cedar_bindings(_py: Python, m: &PyModule) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(parse_policy, m)?)?;
    Ok(())
}
```

#### Option C: cedar-wasm WebAssembly Module

**Approach:** Use Cedar compiled to WebAssembly.

**Pros:**

- Platform-independent binary
- No native compilation needed
- Portable across environments

**Cons:**

- Immature ecosystem
- May not expose all Cedar APIs
- Additional WebAssembly runtime dependency
- Performance may be worse than native

**Status:** Not currently available (as of January 2026)

---

## Recommended Approach: Option A (Cedar CLI)

### Rationale

1. **Mature Tooling**: Cedar CLI is production-ready and actively maintained
2. **Easy Deployment**: Single binary, no build complexity
3. **Compile-Time Operation**: Subprocess overhead is acceptable (policies compiled infrequently)
4. **Future-Proof**: Automatically inherits new Cedar features via CLI upgrades

### Implementation Plan

#### Phase 1: Basic Integration (Week 1)

**Goal:** Replace `_legacy_parse_policy()` with Cedar CLI parsing.

**Tasks:**

1. Add Cedar CLI as deployment dependency
2. Implement `parse_policy_with_cedar()` function
3. Update `compile_policy()` to use new parser
4. Maintain backward compatibility with existing policy format
5. Add CLI availability check with fallback

**Deliverables:**

- ✅ Cedar CLI parsing integration
- ✅ Unit tests for CLI integration
- ✅ Error handling for malformed policies
- ✅ Graceful degradation if CLI unavailable

**Code Location:** [src/raja/cedar/parser.py](../../src/raja/cedar/parser.py)

#### Phase 2: Schema Validation (Week 1-2)

**Goal:** Validate policies against Cedar schema before compilation.

**Tasks:**

1. Load Cedar schema from file
2. Pass schema to `cedar validate` command
3. Reject policies that violate schema constraints
4. Add schema-aware entity reference validation
5. Update compiler to use schema information

**Deliverables:**

- ✅ Schema loading and validation
- ✅ Test 2.7 (schema validation) passes
- ✅ Test 2.6 (principal-action mismatch) passes
- ✅ Integration with AVP schema

**Code Location:** [src/raja/cedar/schema.py](../../src/raja/cedar/schema.py)

#### Phase 3: Forbid Policy Support (Week 2)

**Goal:** Correctly compile and enforce forbid policies.

**Tasks:**

1. Update compiler to handle forbid effect
2. Implement forbid policy precedence (deny overrides permit)
3. Update scope generation to reflect forbid policies
4. Add forbid policy integration tests
5. Document forbid policy semantics

**Deliverables:**

- ✅ Forbid policies compile correctly
- ✅ Test 2.1 (forbid policies) passes
- ✅ Test 2.3 (conflicting policies) passes
- ✅ Forbid takes precedence over permit

**Design Decision:**

**Option 1: Exclude Forbidden Scopes**

- Compile permits: `["S3Bucket:bucket-a:s3:GetObject", "S3Bucket:bucket-b:s3:GetObject"]`
- Compile forbids: `["S3Bucket:bucket-a:s3:GetObject"]`
- Result: Issue token with `["S3Bucket:bucket-b:s3:GetObject"]` only

**Option 2: Fail Compilation**

- Reject policy sets with overlapping permit/forbid
- Force user to resolve conflicts explicitly
- Simpler enforcement (no runtime deny checking)

**Recommendation:** Option 1 (scope exclusion) for flexibility, but log warnings for overlapping policies.

#### Phase 4: Advanced Features (Week 3)

**Goal:** Support Cedar templates, wildcards, and complex conditions.

**Tasks:**

1. Implement policy template instantiation
2. Add wildcard resource pattern expansion
3. Support `when`/`unless` conditions (if feasible)
4. Handle action hierarchies (e.g., `s3:*` → all S3 actions)
5. Update scope generation for complex patterns

**Deliverables:**

- ✅ Test 2.4 (wildcard expansion) passes
- ✅ Test 2.5 (template variables) passes
- ✅ Test 3.5 (wildcard boundaries) passes
- ✅ Documentation of supported Cedar features

**Scope Expansion Examples:**

```cedar
// Template with variable
permit(
  principal == User::"{{user}}",
  action == Action::"s3:GetObject",
  resource in S3Bucket::"{{bucket}}"
);

// Instantiate for user=alice, bucket=my-bucket
// Result: ["S3Object:my-bucket/*:s3:GetObject"]
```

```cedar
// Wildcard action
permit(
  principal == User::"alice",
  action in [Action::"s3:GetObject", Action::"s3:PutObject"],
  resource == S3Bucket::"my-bucket"
);

// Result: Multiple scopes
// ["S3Bucket:my-bucket:s3:GetObject", "S3Bucket:my-bucket:s3:PutObject"]
```

#### Phase 5: Validation and Testing (Week 3-4)

**Goal:** Comprehensive validation of Cedar integration.

**Tasks:**

1. Run full failure mode test suite
2. Validate all 7 Cedar tests pass
3. Add property-based tests for Cedar parsing
4. Stress-test with complex policy sets
5. Performance benchmarking
6. Update documentation

**Deliverables:**

- ✅ All Cedar compilation tests passing (2.1-2.7)
- ✅ Integration tests with AVP
- ✅ Performance benchmarks
- ✅ Updated documentation

**Success Criteria:**

- 24/40 tests passing (up from 17/40)
- No regression in existing tests
- Cedar validation errors are actionable
- Compilation time < 1s for typical policy sets

---

## Migration Strategy

### Backward Compatibility

**Maintain dual-path support during migration:**

```python
def parse_policy(policy_str: str, use_cedar_cli: bool = True) -> ParsedPolicy:
    """Parse Cedar policy with optional CLI integration."""
    if use_cedar_cli and _cedar_cli_available():
        return _parse_with_cedar_cli(policy_str)
    else:
        # Fallback to legacy parser
        warnings.warn("Using legacy Cedar parser (limited features)", DeprecationWarning)
        return _legacy_parse_policy(policy_str)
```

**Feature Flag:**

- Environment variable: `RAJA_USE_CEDAR_CLI=true`
- Gradual rollout via feature flag
- Monitor error rates and performance

### Deployment Requirements

**Cedar CLI Installation:**

```dockerfile
# Add to Dockerfile
RUN cargo install cedar-policy-cli --version 3.0.0

# Verify installation
RUN cedar --version
```

**Lambda Layer (if using AWS Lambda):**

```bash
# Build Cedar CLI for Lambda
cargo build --release --target x86_64-unknown-linux-musl
zip cedar-cli-layer.zip bootstrap
```

### Rollback Plan

If Cedar CLI integration causes issues:

1. Set `RAJA_USE_CEDAR_CLI=false` environment variable
2. Revert to legacy parser immediately
3. No code deployment required (feature flag controlled)
4. Investigate and fix issues before re-enabling

---

## Testing Strategy

### Unit Tests

**Test Categories:**

1. **CLI Integration**: Subprocess handling, error parsing
2. **Policy Parsing**: Permit, forbid, templates, wildcards
3. **Schema Validation**: Valid/invalid entity references
4. **Error Handling**: Malformed policies, missing CLI, schema errors
5. **Backward Compatibility**: Legacy parser still works

**Example Test:**

```python
def test_cedar_cli_rejects_invalid_syntax():
    """Test that Cedar CLI catches syntax errors."""
    invalid_policy = "permit(principal === User::alice, action, resource);"

    with pytest.raises(ValueError, match="syntax error"):
        parse_policy_with_cedar(invalid_policy)
```

### Integration Tests

**Test Scenarios:**

1. **End-to-End Compilation**: Cedar → Scopes → Token → Enforcement
2. **Forbid Precedence**: Overlapping permit/forbid policies
3. **Template Instantiation**: Variable substitution in policies
4. **Schema Enforcement**: Invalid entity references rejected
5. **AVP Consistency**: Local Cedar matches remote AVP policies

**Example Test:**

```python
@pytest.mark.integration
def test_forbid_policy_blocks_permit():
    """Test that forbid takes precedence over permit."""
    permit_policy = 'permit(principal == User::"alice", action, resource);'
    forbid_policy = 'forbid(principal == User::"alice", action == Action::"s3:DeleteObject", resource);'

    scopes = compile_policies([permit_policy, forbid_policy], principal="alice")

    # Should NOT include s3:DeleteObject
    assert not any("DeleteObject" in scope for scope in scopes)
```

### Failure Mode Tests

**Updated Test Status:**

After Cedar integration, these tests should transition from NOT_IMPLEMENTED → PASS:

- ✅ 2.1: Forbid policies
- ✅ 2.2: Policy syntax errors
- ✅ 2.3: Conflicting policies
- ✅ 2.4: Wildcard expansion
- ✅ 2.5: Template variables
- ✅ 2.6: Principal-action mismatch
- ✅ 2.7: Schema validation

**Target:** 24/40 tests passing (60%)

---

## Open Questions

### 1. How to Handle `when`/`unless` Clauses?

**Context:** Cedar supports conditional policies with `when { context.ip == "10.0.0.0/8" }`.

**Options:**

- **A:** Reject policies with conditions (scope model doesn't support runtime context)
- **B:** Compile conditions into separate scopes (e.g., `S3Bucket:bucket:s3:GetObject:ip=10.0.0.0/8`)
- **C:** Evaluate conditions at token issuance time (requires runtime context)

**Recommendation:** Option A initially (reject), document limitation, revisit if needed.

### 2. Action Hierarchy Support?

**Context:** S3 has action hierarchies (e.g., `s3:*` includes all S3 actions).

**Options:**

- **A:** Expand `s3:*` to explicit list of actions at compile time
- **B:** Support wildcard actions in enforcement (`s3:*` → `s3:GetObject`, `s3:PutObject`, etc.)
- **C:** Reject wildcard actions (require explicit action list)

**Recommendation:** Option A (compile-time expansion) for predictability.

### 3. Template Variable Scope?

**Context:** Current parser only supports `{{bucket}}` in resource IDs.

**Expansion Needed:**

- `{{principal}}` in principal clause?
- `{{action}}` in action clause?
- Arbitrary template variables?

**Recommendation:** Support common use cases (`{{user}}`, `{{bucket}}`), document supported variables.

### 4. Schema Update Mechanism?

**Context:** Cedar schema may evolve independently of RAJA.

**Questions:**

- How to sync schema between local files and AVP?
- Versioning strategy for schema changes?
- Backward compatibility for existing policies?

**Recommendation:** Treat AVP as source of truth, fetch schema dynamically, cache locally.

---

## Success Metrics

### Quantitative

1. **Test Coverage**: 24/40 failure mode tests passing (60% → 60%+)
2. **Cedar Tests**: 7/7 Cedar compilation tests passing (0% → 100%)
3. **Compilation Time**: < 1s for typical policy sets
4. **Error Detection**: 100% of invalid Cedar policies rejected before deployment

### Qualitative

1. **Developer Confidence**: Policies validated against official tooling
2. **Security Posture**: Forbid policies enable deny-by-default security
3. **Maintainability**: Less custom code to maintain
4. **Future-Proof**: Easy to adopt new Cedar features

---

## Dependencies

### External

- **Cedar CLI**: Version 3.0.0+ (Rust toolchain for installation)
- **Cedar Schema**: Current schema from AVP or local files
- **Cargo**: For installing Cedar CLI

### Internal

- **Compiler**: [src/raja/compiler.py](../../src/raja/compiler.py)
- **Parser**: [src/raja/cedar/parser.py](../../src/raja/cedar/parser.py)
- **Schema**: [src/raja/cedar/schema.py](../../src/raja/cedar/schema.py)
- **Enforcer**: [src/raja/enforcer.py](../../src/raja/enforcer.py) (may need forbid support)

---

## Timeline

**Total Estimated Time:** 3-4 weeks (1 engineer)

| Phase | Duration | Deliverable |
|-------|----------|-------------|
| 1. Basic Integration | 1 week | Cedar CLI parsing working |
| 2. Schema Validation | 1 week | Schema-aware validation |
| 3. Forbid Support | 1 week | Forbid policies enforced |
| 4. Advanced Features | 1 week | Templates, wildcards |
| 5. Testing & Docs | 1 week | All tests passing, documented |

**Parallelization Opportunities:**

- Schema validation (Phase 2) can overlap with forbid support (Phase 3)
- Testing can begin incrementally during implementation

---

## References

### Cedar Documentation

- **Cedar Language**: <https://docs.cedarpolicy.com/>
- **Cedar CLI**: <https://github.com/cedar-policy/cedar/tree/main/cedar-policy-cli>
- **Cedar Rust Crate**: <https://docs.rs/cedar-policy/>
- **Schema Format**: <https://docs.cedarpolicy.com/schema/schema.html>

### RAJA Documentation

- [06-failure-fixes.md](06-failure-fixes.md) - Section 1: Cedar compilation failures
- [08-remaining-work.md](08-remaining-work.md) - Section 1: Cedar policy compilation
- [src/raja/cedar/parser.py](../../src/raja/cedar/parser.py) - Current parser implementation
- [tests/unit/test_cedar_parser.py](../../tests/unit/test_cedar_parser.py) - Parser unit tests

### Related Issues

- Failure tests 2.1-2.7 (Cedar compilation)
- Failure test 3.5 (wildcard boundaries)
- Integration test: `test_avp_policy_store_matches_local_files`

---

## Next Actions

1. ✅ **This Document** - Cedar integration plan written
2. ⏭️ **Spike**: Install Cedar CLI and prototype basic parsing (1 day)
3. ⏭️ **Decision**: Confirm Option A (Cedar CLI) vs Option B (PyO3 bindings)
4. ⏭️ **Implementation**: Begin Phase 1 (basic integration)
5. ⏭️ **Testing**: Validate test 2.2 (policy syntax errors) passes with Cedar CLI

---

## Appendix: Cedar CLI Examples

### Basic Validation

```bash
# Validate a single policy
cedar validate --schema schema.cedar --policy policy.cedar

# Validate policy from stdin
echo 'permit(principal, action, resource);' | cedar validate --schema schema.cedar --policy -

# JSON output for programmatic parsing
cedar validate --schema schema.cedar --policy policy.cedar --output-format json
```

### Schema Validation

```bash
# Validate schema syntax
cedar validate-schema schema.cedar

# Check policy against schema
cedar validate --schema schema.cedar --policy policy.cedar
```

### Error Output

```json
{
  "errors": [
    {
      "policy_id": "policy0",
      "error": "unexpected token: expected ';' but found 'action'",
      "location": {
        "line": 1,
        "column": 45
      }
    }
  ]
}
```

### Template Instantiation

```bash
# Templates require linking with entities
cedar link-template --template template.cedar --values values.json

# Example values.json
{
  "user": "alice",
  "bucket": "my-bucket"
}
```

---

**Document Status:** Complete
**Next Review:** After Phase 1 completion
**Owner:** RAJA Team
