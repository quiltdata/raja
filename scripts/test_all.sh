#!/usr/bin/env bash
set -euo pipefail

fail_on_missing=false
if [ "${CI:-}" = "true" ] || [ "${GITHUB_ACTIONS:-}" = "true" ]; then
  fail_on_missing=true
fi

echo "==> Running Python unit tests"
pytest tests/unit/ -v

echo "==> Running Cedar Rust validation"
if command -v cargo >/dev/null 2>&1; then
  cargo run --quiet --bin cedar-validate --manifest-path tools/cedar-validate/Cargo.toml -- policies
else
  if [ "$fail_on_missing" = "true" ]; then
    echo "cargo is required for Cedar validation but is not available" >&2
    exit 1
  fi
  echo "Skipping Cedar validation: cargo not found"
fi

echo "==> Running Lua tests"
if command -v busted >/dev/null 2>&1; then
  busted tests/lua/authorize_spec.lua
else
  if [ "$fail_on_missing" = "true" ]; then
    echo "busted is required for Lua tests but is not available" >&2
    exit 1
  fi
  echo "Skipping Lua tests: busted not found"
fi
