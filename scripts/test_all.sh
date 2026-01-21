#!/usr/bin/env bash
set -euo pipefail

echo "==> Running Python unit tests"
pytest tests/unit/ -v

echo "==> Running Cedar Rust validation"
if ! command -v cargo >/dev/null 2>&1; then
  echo "ERROR: cargo is required for Cedar validation but is not available" >&2
  exit 1
fi
cargo run --quiet --bin cedar-validate --manifest-path tools/cedar-validate/Cargo.toml -- policies

echo "==> Running Lua tests"
if ! command -v busted >/dev/null 2>&1; then
  echo "ERROR: busted is required for Lua tests but is not available" >&2
  exit 1
fi
busted tests/lua/authorize_spec.lua
