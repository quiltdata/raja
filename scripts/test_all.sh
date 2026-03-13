#!/usr/bin/env bash
set -euo pipefail

echo "==> Running Python unit tests"
pytest tests/unit/ -v

echo "==> Running Lua tests"
if ! command -v busted >/dev/null 2>&1; then
  echo "ERROR: busted is required for Lua tests but is not available" >&2
  exit 1
fi
busted tests/lua/authorize_spec.lua
