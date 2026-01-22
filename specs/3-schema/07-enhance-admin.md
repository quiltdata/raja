# Admin Interface Enhancement for Failure Mode Testing

## Purpose

Extend the RAJA Admin interface to provide interactive testing of all failure modes documented in [03-failure-modes.md](03-failure-modes.md). This creates a visual validation harness that complements automated tests and enables manual exploration of edge cases.

## Objectives

1. **Interactive Validation**: Test all 62 failure modes through the web UI
2. **Developer Experience**: Quick visual confirmation of security boundaries
3. **Documentation Aid**: Live examples of what should fail and why
4. **Regression Testing**: Manual verification when automated tests are insufficient

## Design Principles

### 1. Fail-First Testing

- Every test case expects a specific failure
- Success is when the system correctly rejects the request
- UI clearly distinguishes "expected failure" from "unexpected success"

### 2. Categorized Test Suites

- Mirror the structure of [03-failure-modes.md](03-failure-modes.md)
- Six test categories with expandable sections
- Visual indication of critical vs high vs medium priority

### 3. One-Click Execution

- Pre-configured test cases with "Run Test" buttons
- No manual token/scope/request construction required
- Side-by-side comparison: expected vs actual behavior

### 4. Detailed Result Display

- Show the full request/response cycle
- Highlight why the failure occurred
- Link back to failure mode documentation

## Architecture

### New UI Sections

Add to existing [admin.html](../../src/raja/server/templates/admin.html):

```
┌─────────────────────────────────────┐
│  Existing Sections (unchanged)      │
│  - Issuer & JWKS                    │
│  - Mint RAJ                         │
│  - Verify RAJ                       │
│  - Simulate Enforcement             │
│  - Control Plane                    │
└─────────────────────────────────────┘
         │
         ▼
┌─────────────────────────────────────┐
│  NEW: Failure Mode Test Suite       │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ Category Selection          │   │
│  │ [Token] [Cedar] [Scope]     │   │
│  │ [Request] [Cross] [Ops]     │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ Test Case Browser           │   │
│  │ • 1.1 Expired Token [CRIT]  │   │
│  │ • 1.2 Invalid Sig   [CRIT]  │   │
│  │ • 1.3 Malformed JWT [CRIT]  │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ Test Execution              │   │
│  │ [Run Test] [Run Category]   │   │
│  └─────────────────────────────┘   │
│                                     │
│  ┌─────────────────────────────┐   │
│  │ Results Display             │   │
│  │ Expected: DENY (expired)    │   │
│  │ Actual:   DENY (expired)    │   │
│  │ Status:   ✅ PASS           │   │
│  └─────────────────────────────┘   │
└─────────────────────────────────────┘
```

### Backend API Extensions

Add new endpoints to existing server:

- `GET /api/failure-tests` - List all test cases with metadata
- `POST /api/failure-tests/{test_id}` - Execute specific test
- `POST /api/failure-tests/category/{category}` - Run all tests in category
- `GET /api/failure-tests/results/{run_id}` - Retrieve test run results

## Test Case Specification

### Test Case Structure

Each test case from [03-failure-modes.md](03-failure-modes.md) maps to:

```json
{
  "id": "1.1",
  "category": "token-security",
  "priority": "CRITICAL",
  "title": "Expired Token",
  "description": "Expired JWT passes validation in production",
  "reference": "03-failure-modes.md:27-37",

  "setup": {
    "type": "mint-tamper",
    "modifications": {
      "exp": "past-timestamp"
    }
  },

  "execution": {
    "endpoint": "/token/verify",
    "method": "POST",
    "payload": {
      "token": "${tampered_token}",
      "audience": "raja-s3"
    }
  },

  "expected": {
    "status": 401,
    "body_contains": "expired",
    "decision": "DENY",
    "reason": "Token expiration check failed"
  }
}
```

### Test Categories

Mirror the six categories from [03-failure-modes.md](03-failure-modes.md):

#### 1. Token Security (6 tests)

- 1.1 Expired Token [CRITICAL]
- 1.2 Invalid Signature [CRITICAL]
- 1.3 Malformed JWT [CRITICAL]
- 1.4 Missing/Empty Scopes [HIGH]
- 1.5 Token Claim Validation [HIGH]
- 1.6 Token Revocation [MEDIUM]

**UI Treatment:**

- Red priority badge for CRITICAL
- Orange for HIGH
- Yellow for MEDIUM
- Auto-expand CRITICAL tests on page load

#### 2. Cedar Policy Compilation (7 tests)

- 2.1 Forbid Policies [CRITICAL]
- 2.2 Template Injection [CRITICAL]
- 2.3 Complex When Clauses [HIGH]
- 2.4 Principal In Clauses [HIGH]
- 2.5 Action In Clauses [HIGH]
- 2.6 Multiple In Clauses [MEDIUM]
- 2.7 Invalid Entity Hierarchies [MEDIUM]

**UI Treatment:**

- Code syntax highlighting for Cedar snippets
- "View Policy" button to see full Cedar source
- Compilation output diff (expected scopes vs actual)

#### 3. Scope Enforcement (8 tests)

- 3.1 Malformed Scope Format [CRITICAL]
- 3.2 Bucket Prefix Matching [CRITICAL]
- 3.3 Special Characters [HIGH]
- 3.4 Empty Components [HIGH]
- 3.5 Type Mismatches [MEDIUM]
- 3.6 Missing Action [MEDIUM]
- 3.7 Trailing Slash Ambiguity [MEDIUM]
- 3.8 Substring vs Prefix [LOW]

**UI Treatment:**

- Visual scope breakdown: `[Type] : [Resource] : [Action]`
- Highlight the component causing failure
- Show scope matching logic step-by-step

#### 4. Request Parsing (5 tests)

- 4.1 Missing Bucket/Key [CRITICAL]
- 4.2 Query Parameter Injection [HIGH]
- 4.3 Unknown S3 Actions [HIGH]
- 4.4 Path Traversal [MEDIUM]
- 4.5 Malformed Query Strings [MEDIUM]

**UI Treatment:**

- HTTP request preview with syntax highlighting
- Query string parser visualization
- Path normalization display

#### 5. Cross-Component (6 tests)

- 5.1 Cedar → Scopes Traceability [CRITICAL]
- 5.2 Policy Updates vs Existing Tokens [CRITICAL]
- 5.3 Scope Deduplication [CRITICAL]
- 5.4 Template Expansion Context [HIGH]
- 5.5 Principal-to-Scopes Mapping [HIGH]
- 5.6 AVP Policy Store Consistency [MEDIUM]

**UI Treatment:**

- Flow diagram showing data path
- Diff view for policy changes
- Timeline for policy update propagation

#### 6. Operational (7 tests)

- 6.1 Authorization Decision Logging [HIGH]
- 6.2 Performance [HIGH]
- 6.3 Concurrent Requests [MEDIUM]
- 6.4 Large Token Scopes [MEDIUM]
- 6.5 Envoy Lua Memory [MEDIUM]
- 6.6 Error Response Formats [MEDIUM]
- 6.7 Health Check Validation [LOW]

**UI Treatment:**

- Performance metrics display (P50/P99 latency)
- Concurrent request simulator
- Token size calculator
- Log stream viewer

## UI Components

### 1. Test Category Selector

**Location:** Top of new section

**Visual Design:**

- Horizontal pill navigation
- Badge count showing total tests per category
- Color coding by highest priority in category
- Keyboard navigation (arrow keys)

**Behavior:**

- Click to expand category
- Maintains state in URL hash (`#token-security`)
- Shows summary: X/Y tests passing

### 2. Test Case Card

**Layout:**

```
┌────────────────────────────────────────┐
│ [CRIT] 1.1 Expired Token            [▼]│
├────────────────────────────────────────┤
│ Description:                           │
│ Expired JWT passes validation          │
│                                        │
│ Reference: 03-failure-modes.md:27      │
│                                        │
│ [Run Test]  [View Code]  [View Spec]   │
└────────────────────────────────────────┘
```

**States:**

- Collapsed: Shows title + priority
- Expanded: Shows description + actions
- Running: Spinner + progress indicator
- Pass: Green checkmark + summary
- Fail: Red X + detailed error

### 3. Test Execution Panel

**Pre-Execution:**

- Shows what will be tested
- Displays setup steps (e.g., "Minting token with past expiration")
- Editable parameters (advanced mode)

**During Execution:**

- Real-time progress updates
- HTTP request/response preview
- Streaming logs (if available)

**Post-Execution:**

- Expected vs Actual comparison
- Pass/Fail status with explanation
- Raw response data (expandable)
- "Copy as curl" button

### 4. Results Summary

**Location:** Sticky header during test runs

**Display:**

```
Running: Token Security Tests (3/6)
Passed: 2  Failed: 1  Pending: 3
[█████████░░░░░░] 50%
```

**Persistence:**

- Results stored in localStorage
- "Export Results" button (JSON/CSV)
- "Share Results" generates permalink

### 5. Batch Test Runner

**Feature:** Run multiple tests sequentially

**UI:**

- Checkbox selection in test list
- "Run Selected (N)" button
- Parallel vs Sequential toggle
- Stop button to abort run

**Results:**

- Matrix view showing all results
- Filter by pass/fail/priority
- Export to test report format

## Test Case Implementation

### Test Setup Strategies

#### Strategy 1: Token Tampering

For tests 1.1, 1.2, 1.3, 1.4, 1.5:

1. Mint valid token via existing "Mint RAJ" form
2. Decode token to JSON
3. Modify specific claim (exp, signature, scopes, etc.)
4. Re-encode token (optionally with wrong secret)
5. Attempt to use tampered token

**Backend Support:**

- New endpoint: `POST /api/tamper-token`
- Parameters: `token`, `modification_type`, `modification_value`
- Returns: Modified token + explanation of tampering

#### Strategy 2: Policy Compilation

For tests 2.1-2.7:

1. Load Cedar policy with problematic construct
2. Attempt compilation via compiler Lambda
3. Check compilation result (error or unexpected scopes)
4. Compare against expected behavior

**Backend Support:**

- Enhance `POST /api/compile` to accept inline Cedar
- Return compilation diagnostics + scope list
- Flag unexpected behaviors (e.g., forbid ignored)

#### Strategy 3: Scope Validation

For tests 3.1-3.8:

1. Manually construct malformed scope string
2. Inject into token claims
3. Attempt enforcement
4. Verify rejection with specific error

**Backend Support:**

- New endpoint: `POST /api/validate-scope`
- Parameters: `scope_string`
- Returns: Parse result, validation errors, normalized form

#### Strategy 4: Request Simulation

For tests 4.1-4.5:

1. Construct S3-like HTTP request
2. Send to enforcement endpoint
3. Check parsing and decision
4. Verify correct error handling

**Backend Support:**

- Enhance existing enforce endpoint
- Add detailed request parsing diagnostics
- Return parse tree or error details

#### Strategy 5: Cross-Component Trace

For tests 5.1-5.6:

1. Execute multi-step flow (policy → scope → token → decision)
2. Capture intermediate results at each stage
3. Validate consistency across components
4. Detect silent failures or mismatches

**Backend Support:**

- New endpoint: `POST /api/trace-flow`
- Parameters: `policy_id`, `principal`, `request`
- Returns: Full execution trace with timestamps

#### Strategy 6: Operational Metrics

For tests 6.1-6.7:

1. Execute request under observation
2. Capture logs, metrics, response formats
3. Validate operational invariants
4. Check performance characteristics

**Backend Support:**

- New endpoint: `GET /api/metrics`
- Returns: Recent decision logs, latency stats, error counts
- Streaming endpoint for real-time logs

## Non-Functional Requirements

### Performance

- Test execution < 2 seconds per test
- Batch runs < 30 seconds for all 62 tests
- UI remains responsive during test runs
- Results cached for instant replay

### Usability

- Zero-config: All tests work out of the box
- Progressive disclosure: Simple view by default, details on demand
- Mobile-friendly: Touch targets, responsive layout
- Keyboard shortcuts: Space to run, J/K to navigate

### Accessibility

- ARIA labels on all interactive elements
- Keyboard navigation throughout
- Screen reader announcements for test results
- High contrast mode support

### Maintainability

- Test definitions in separate JSON file
- Easy to add new test cases
- Automated sync with [03-failure-modes.md](03-failure-modes.md)
- Version control for test suite

## Implementation Phases

### Phase 1: Foundation (MVP)

**Scope:**

- Add new "Failure Mode Tests" section to admin.html
- Implement test category selector
- Add 6 critical token security tests (1.1-1.3 priority)
- Basic pass/fail display

**Success Criteria:**

- Can run expired token test through UI
- Clear visual feedback on expected vs actual
- Results persist across page reloads

### Phase 2: Cedar & Scope Tests

**Scope:**

- Add Cedar compilation tests (2.1-2.7)
- Add scope enforcement tests (3.1-3.8)
- Enhance results display with code highlighting
- Add batch test runner

**Success Criteria:**

- Can test forbid policy handling
- Can test bucket prefix matching
- Can run entire category at once

### Phase 3: Cross-Component & Operational

**Scope:**

- Add request parsing tests (4.1-4.5)
- Add cross-component tests (5.1-5.6)
- Add operational tests (6.1-6.7)
- Implement full trace view

**Success Criteria:**

- Can trace policy → token → decision
- Can measure authorization latency
- Can export test reports

### Phase 4: Polish & Integration

**Scope:**

- Test result permalinks
- Export to automated test format
- Integration with CI/CD
- Documentation and tutorials

**Success Criteria:**

- Test results shareable via URL
- Can generate pytest code from test case
- All 62 tests documented and executable

## Open Questions

### 1. Test Data Management

**Question:** How to handle test data that requires AWS resources (AVP policies, DynamoDB state)?

**Options:**

- A) Use existing deployed resources (may conflict with real data)
- B) Create isolated test namespace in same deployment
- C) Mock AWS responses in admin server
- D) Require separate test deployment

**Recommendation:** Option B - namespace test data with `test-` prefix

### 2. Test Isolation

**Question:** How to prevent test pollution between runs?

**Options:**

- A) Clean up after each test (may miss failures)
- B) Use unique IDs per test run (fills up DynamoDB)
- C) Use test fixture setup/teardown
- D) Accept non-idempotent tests

**Recommendation:** Option C - explicit setup/teardown per test

### 3. Result Persistence

**Question:** Where to store test run history?

**Options:**

- A) Browser localStorage (limited capacity)
- B) DynamoDB table (cost, retention)
- C) S3 bucket (delay, complexity)
- D) Ephemeral only (no history)

**Recommendation:** Option A for recent runs + Option B for long-term

### 4. Authentication

**Question:** Should failure mode testing require authentication?

**Options:**

- A) Public access (security risk)
- B) Same auth as admin interface (reuse existing)
- C) Separate admin credentials (complexity)
- D) IP allowlist only (inflexible)

**Recommendation:** Option B - reuse existing admin auth

### 5. Test Coverage Tracking

**Question:** How to track which tests cover which failure modes?

**Options:**

- A) Manual mapping in test definitions
- B) Automated analysis of [03-failure-modes.md](03-failure-modes.md)
- C) No tracking (just run all tests)
- D) Integration with pytest test markers

**Recommendation:** Option B + Option D - sync with both sources

## Success Metrics

### Developer Adoption

- 80% of failure modes testable via UI
- < 5 minutes to reproduce any failure mode
- Zero external tools required

### Quality Assurance

- All critical tests passing before deployment
- Visual regression detection (UI shows unexpected behavior)
- Test results included in release notes

### Documentation

- Failure mode spec references live examples
- New team members use UI to learn authorization boundaries
- Test cases serve as executable documentation

## Future Enhancements

### Integration Testing

- Run failure tests against live Envoy proxy
- End-to-end S3 request simulation
- Multi-region consistency testing

### Test Generation

- Auto-generate test cases from Cedar policies
- Fuzzing integration (random malformed inputs)
- Property-based test case generation

### Monitoring Integration

- Alert on failure mode regression in production
- Dashboard showing real-world failure mode occurrences
- Automated incident correlation with test cases

### Developer Tools

- Browser extension for quick test execution
- CLI tool to run tests locally
- IDE plugin for inline test results

## References

### Related Documents

- [03-failure-modes.md](03-failure-modes.md) - Comprehensive failure mode catalog
- [01-bucket-object.md](01-bucket-object.md) - S3 bucket/object schema design
- [02-cedar-impl.md](02-cedar-impl.md) - Cedar implementation details
- [06-failure-fixes.md](06-failure-fixes.md) - Solutions to identified failures

### Existing Code

- [admin.html](../../src/raja/server/templates/admin.html) - Current admin interface
- [admin.js](../../src/raja/server/static/admin.js) - Current admin client code
- [admin.css](../../src/raja/server/static/admin.css) - Current admin styles
- [server.py](../../src/raja/server/server.py) - Flask server with existing endpoints

### External Resources

- Cedar Policy Language: <https://www.cedarpolicy.com/>
- S3 API Reference: <https://docs.aws.amazon.com/AmazonS3/latest/API/>
- JWT Best Practices: <https://datatracker.ietf.org/doc/html/rfc8725>

## Appendix: Test Case Examples

### Example 1: Expired Token Test

```json
{
  "id": "1.1",
  "title": "Expired Token",
  "priority": "CRITICAL",
  "category": "token-security",

  "steps": [
    {
      "action": "mint",
      "params": {
        "subject": "User::test",
        "scopes": ["S3Object:bucket/key:s3:GetObject"],
        "ttl": -60
      },
      "description": "Mint token with expiration 60 seconds in the past"
    },
    {
      "action": "verify",
      "params": {
        "token": "${minted_token}",
        "audience": "raja-s3"
      },
      "description": "Attempt to verify expired token"
    }
  ],

  "expected": {
    "verify_result": "FAIL",
    "error_contains": "expired",
    "status_code": 401
  },

  "pass_criteria": "Token verification rejects expired token with clear error"
}
```

### Example 2: Forbid Policy Test

```json
{
  "id": "2.1",
  "title": "Forbid Policies",
  "priority": "CRITICAL",
  "category": "cedar-compilation",

  "steps": [
    {
      "action": "compile",
      "params": {
        "policy": "forbid(principal == User::\"alice\", action == Action::\"s3:DeleteObject\", resource == S3Object::\"protected/\") when { resource in S3Bucket::\"test-bucket\" };",
        "principal": "User::alice"
      },
      "description": "Compile forbid policy"
    },
    {
      "action": "check_compilation",
      "params": {
        "expected_behavior": "error"
      },
      "description": "Verify compiler rejects or errors on forbid"
    }
  ],

  "expected": {
    "compilation_result": "ERROR",
    "error_contains": "forbid not supported",
    "scopes_generated": []
  },

  "pass_criteria": "Compiler explicitly rejects forbid policies"
}
```

### Example 3: Bucket Prefix Matching Test

```json
{
  "id": "3.2",
  "title": "Bucket Prefix Matching",
  "priority": "CRITICAL",
  "category": "scope-enforcement",

  "steps": [
    {
      "action": "create_scope",
      "params": {
        "scope": "S3Object:raja-poc-test-/key:s3:GetObject"
      },
      "description": "Create scope with trailing dash (looks like prefix)"
    },
    {
      "action": "mint",
      "params": {
        "subject": "User::test",
        "scopes": ["${created_scope}"]
      },
      "description": "Mint token with prefix-like scope"
    },
    {
      "action": "enforce",
      "params": {
        "token": "${minted_token}",
        "bucket": "raja-poc-test-different-account",
        "key": "key",
        "action": "s3:GetObject"
      },
      "description": "Try to access different bucket"
    }
  ],

  "expected": {
    "decision": "DENY",
    "reason": "bucket must match exactly",
    "matched_scopes": []
  },

  "pass_criteria": "Bucket with trailing dash does NOT match different bucket names"
}
```

## Implementation Notes

### Backend API Design

All new endpoints follow REST conventions:

- `GET /api/failure-tests` - List all test cases
- `GET /api/failure-tests/{test_id}` - Get test definition
- `POST /api/failure-tests/{test_id}/run` - Execute test
- `GET /api/failure-tests/runs/{run_id}` - Get run results
- `POST /api/failure-tests/categories/{category}/run` - Run category
- `DELETE /api/failure-tests/runs/{run_id}` - Clean up test data

### State Management

Test execution state machine:

```
IDLE → SETUP → RUNNING → VALIDATING → COMPLETE
         ↓        ↓           ↓
       ERROR    ERROR      ERROR
```

Each state transition logged for debugging.

### Error Handling

Test failures vs system failures:

- **Test Failure**: System correctly rejected invalid input (EXPECTED)
- **System Failure**: Test couldn't run due to infrastructure issue (UNEXPECTED)
- **Validation Failure**: Expected behavior doesn't match actual (BUG)

UI clearly distinguishes these three cases.

### Security Considerations

- Failure tests may generate malicious inputs (expired tokens, injection attempts)
- Admin interface must not expose these to unauthorized users
- Test results should not leak sensitive information (policy details, principals)
- Rate limiting on test execution to prevent DoS

---

**Document Status:** Draft Specification

**Next Steps:**

1. Review spec with team
2. Validate test case structure with existing test suite
3. Prototype Phase 1 (MVP) in admin interface
4. Implement backend API endpoints
5. Iterate based on developer feedback
