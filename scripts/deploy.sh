#!/usr/bin/env bash
set -euo pipefail

uv sync
uv pip install aws-cdk-lib

pushd infra >/dev/null
cdk synth
cdk deploy --all
popd >/dev/null
