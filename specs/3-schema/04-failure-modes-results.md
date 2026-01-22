# RAJA Failure Modes - Test Results

This document records which newly added failure-mode tests failed after implementation.
It is a sequel to `specs/3-schema/03-failure-modes.md` and does not propose fixes.

## Test Runs

- `pytest tests/unit/ -v`
- `pytest tests/integration/ -v`
- `busted tests/lua/authorize_spec.lua` (not available on system; command not found)

## Unit Test Failures

These failures reflect limitations in the current custom, regex-based Cedar parser/compiler.
Recommendation: replace local parsing/compilation with the standard Cedar Rust compiler (or
another official Cedar toolchain) and gate tests based on `cargo` availability.

### Cedar Parser / Compiler

- `tests/unit/test_cedar_parser.py::test_parse_policy_supports_principal_in_clause`
  - Parser does not recognize `principal in` clauses.
- `tests/unit/test_cedar_parser.py::test_parse_policy_supports_action_in_clause`
  - Parser does not recognize `action in` clauses.
- `tests/unit/test_cedar_parser.py::test_parse_policy_supports_multiple_in_clauses`
  - Parser only accepts a single `resource in` clause.
- `tests/unit/test_compiler.py::test_compile_policy_forbid_rejected`
  - Compiler ignores forbid policies instead of rejecting.
- `tests/unit/test_compiler.py::test_compile_policy_rejects_double_template_expansion`
  - Template expansion accepts `{{account}}{{account}}` concatenation.
- `tests/unit/test_compiler.py::test_compile_policy_rejects_complex_when_clause`
  - Compiler ignores complex `when` conditions.
- `tests/unit/test_compiler.py::test_compile_policy_supports_action_in_clause`
  - Compiler does not expand multiple actions.
- `tests/unit/test_compiler.py::test_compile_policy_supports_principal_in_clause`
  - Compiler does not expand principal groups/roles.
- `tests/unit/test_compiler.py::test_compile_policy_supports_multiple_in_clauses`
  - Compiler does not expand multiple parent buckets.

### Scope Parsing / Token Validation

- `tests/unit/test_scope.py::test_parse_scope_rejects_colon_in_resource_id`
  - Scope parsing treats extra colons as part of the action segment.
- `tests/unit/test_token.py::test_validate_token_rejects_non_list_scopes`
  - Token validation accepts string `scopes` and coerces to list of characters.

## Integration Test Failures

### Token Security / Envoy JWT Filter

- `tests/integration/test_failure_modes.py::test_envoy_rejects_expired_token`
  - Expired token accepted (received 200).
- `tests/integration/test_failure_modes.py::test_envoy_denies_null_scopes`
  - Token with `scopes: null` accepted (received 200).
- `tests/integration/test_failure_modes.py::test_envoy_rejects_wrong_audience`
  - Wrong audience produces 403 (Lua deny) instead of 401 (JWT filter reject).
- `tests/integration/test_failure_modes.py::test_envoy_rejects_missing_subject`
  - Token without `sub` accepted (received 200).

### Cross-Component Validation

- `tests/integration/test_failure_modes.py::test_token_revocation_endpoint_available`
  - `/token/revoke` missing (404).
- `tests/integration/test_failure_modes.py::test_policy_to_token_traceability`
  - `compile_policy` fails when `{{account}}` / `{{region}}` env vars are unset.
- `tests/integration/test_failure_modes.py::test_policy_update_invalidates_existing_token`
  - Old token remains valid after principal scopes update.
- `tests/integration/test_failure_modes.py::test_avp_policy_store_matches_local_files`
  - Local policy statements (with templates) do not match remote AVP statements (expanded).

### Operational Validation

- `tests/integration/test_failure_modes.py::test_error_response_format_is_s3_compatible`
  - Envoy returns 401 + `text/plain`, not S3-compatible XML 403.
- `tests/integration/test_failure_modes.py::test_health_check_verifies_dependencies`
  - `/health` only returns `{ "status": "ok" }` without dependency checks.

## Lua Test Status

- Lua tests could not be executed because `busted` is not installed in this environment.
