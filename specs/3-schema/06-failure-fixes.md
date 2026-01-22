# Failure Fixes Plan

This document lists the remaining fixes implied by
`specs/3-schema/04-failure-modes-results.md`. It focuses on what to change
and how to validate, without prescribing implementation details.

## 1. Replace Custom Cedar Parser/Compiler

### Work
- Remove or deprecate the regex-based Cedar parser and compiler for
  validation/compilation paths.
- Use the standard Cedar Rust tooling for policy parsing/validation.
- Align local compilation with official Cedar semantics.

### Acceptance Criteria
- `principal in`, `action in`, and complex `when` clauses parse successfully.
- Forbid policies are explicitly rejected or handled by design (no silent ignore).
- Template handling follows Cedar semantics and fails when unresolved.

## 2. Scope Parsing and Token Validation

### Work
- Enforce strict scope parsing: colons in resource IDs should be rejected.
- Validate token `scopes` type: reject non-list values.

### Acceptance Criteria
- `tests/unit/test_scope.py::test_parse_scope_rejects_colon_in_resource_id` passes.
- `tests/unit/test_token.py::test_validate_token_rejects_non_list_scopes` passes.

## 3. Envoy JWT Filter + Lua Enforcement Gaps

### Work
- Ensure expired tokens are rejected at the JWT layer.
- Reject missing `sub` or invalid audiences at the JWT layer.
- Treat `scopes: null` as invalid and deny.

### Acceptance Criteria
- `test_envoy_rejects_expired_token` returns 401.
- `test_envoy_rejects_missing_subject` returns 401.
- `test_envoy_rejects_wrong_audience` returns 401.
- `test_envoy_denies_null_scopes` returns 403.

## 4. Token Revocation + Policy Update Semantics

### Work
- Implement token revocation endpoint or remove the test and document
  that revocation is unsupported.
- Ensure policy updates invalidate existing tokens, or explicitly document
  and test the current semantics (no revocation).

### Acceptance Criteria
- `/token/revoke` exists and returns 200 with clear behavior, or the
  test is updated to assert "not supported".
- `test_policy_update_invalidates_existing_token` is aligned with actual behavior.

## 5. Cross-Component Traceability

### Work
- Provide a reliable trace from Cedar policy -> compiled scopes ->
  token scopes -> enforcement decision.
- Ensure template expansion uses correct context (account/region).

### Acceptance Criteria
- `test_policy_to_token_traceability` passes with correct env context.
- Compiled scopes match expected tokens for principals.

## 6. AVP Policy Store Consistency

### Work
- Normalize local and remote policy statements consistently, accounting
  for template expansion.
- Alternatively, compare after expanding templates locally.

### Acceptance Criteria
- `test_avp_policy_store_matches_local_files` passes.

## 7. Error Response Format and Health Checks

### Work
- Return S3-compatible XML error bodies for authorization failures.
- Add health check dependency verification (JWT secret, Lua filter, etc).

### Acceptance Criteria
- `test_error_response_format_is_s3_compatible` returns 403 + XML.
- `test_health_check_verifies_dependencies` includes dependency status.
