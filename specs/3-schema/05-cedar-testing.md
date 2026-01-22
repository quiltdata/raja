# Cedar Testing Proposal (Rust + Lua)

## Goal

Standardize Cedar policy validation and S3 authorization Lua testing using
official toolchains, while keeping `./poe test` comprehensive and fail-fast
when required tools are missing.

## Why Change

The current Cedar parsing/compilation is custom, regex-based Python logic.
It cannot reliably handle `principal in`, `action in`, or complex `when` clauses,
and it diverges from Cedar's official semantics. Tests now reflect this gap.

## Proposal Overview

1) **Cedar Compilation/Validation**
   - Use Cedar's official Rust tooling for parsing/validation.
   - Treat Cedar validation tests as unit tests that run locally without AWS.
   - Skip only when `cargo` (or `rustc`) is missing, but in CI require it and fail if absent.

2) **Lua Authorization Tests**
   - Keep Lua tests in `tests/lua/` and run them with `busted`.
   - Require `busted` in CI and fail if missing.
   - Optional: provide a wrapper to integrate Lua tests into `./poe test`.

## Implementation Details

### A. Cedar Rust Tooling

- Add a lightweight test runner that:
  - checks `cargo --version` (or `rustc --version`)
  - runs Cedar validation via the Rust CLI or a small Rust harness
  - exposes failures to pytest or the `poe test` workflow

Suggested behavior:
- Local dev: if `cargo` missing, mark tests as skipped.
- CI: install Rust and fail if `cargo` is unavailable.

### B. Lua Testing with `busted`

- Run `busted tests/lua/authorize_spec.lua` as part of `./poe test`.
- Local dev: if `busted` missing, skip with a clear message.
- CI: install `busted` and fail if not present.

Note: the tool is named `busted` (if "buster" was intended, use `busted`).

## CI Requirements

Add the following to the CI job that runs unit tests:
- Install Rust toolchain (e.g., `actions/setup-rust` or `dtolnay/rust-toolchain`).
- Install Lua + `busted`:
  - Ubuntu: `apt-get install -y lua5.1 luarocks` + `luarocks install busted`
  - macOS: `brew install lua` + `luarocks install busted`

CI should fail if either Rust or `busted` is missing.

## Proposed `./poe test` Behavior

`./poe test` should:
1) Run pytest unit tests (Python).
2) Run Cedar Rust validation tests (fail if `cargo` missing in CI).
3) Run `busted` Lua tests (fail if `busted` missing in CI).

Optional: provide `./poe test-unit` to run only Python unit tests, and
`./poe test-all` to include Rust + Lua.

## Non-Goals

- No AWS dependency for Cedar validation.
- No policy compilation changes in this document (only testing strategy).
