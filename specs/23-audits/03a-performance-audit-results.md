# Performance Audit Results

Scope reviewed: `infra/envoy/`, `tests/integration/`, `.github/workflows/ci.yml`, `pyproject.toml`, and the local deployment outputs present in the repo.

## Findings

| Severity | File | Line | Issue | What needs fixing |
|---|---|---:|---|---|
| High | `infra/envoy/docker-compose.local.yml` | 4-21 | The local Envoy/JWKS benchmark stack does not start as written. `docker-compose up` fails immediately because both service build contexts resolve outside the repo (`lstat /Users/ernest/tests: no such file or directory`). | The local benchmark bootstrap must be runnable without path errors so the Envoy filter chain can be measured. |
| Medium | `pyproject.toml` | 76-94 | There is no registered `performance` pytest marker, even though the audit spec requires a `@pytest.mark.performance` benchmark suite. With `--strict-markers`, any future perf test would be invalid unless the marker is declared. | The test configuration needs a first-class performance marker so benchmark tests can exist and be collected consistently. |
| Medium | `.github/workflows/ci.yml` | 12-109 | CI has quality, unit, and build jobs only. There is no performance regression job, no P99 threshold gate, and no artifact collection for benchmark output. | CI needs a dedicated performance gate so the JWT+Lua filter chain latency can be tracked and prevented from regressing. |
| Medium | `infra/envoy/entrypoint.sh` and `infra/envoy/authorize.lua` | `entrypoint.sh:186-207`, `authorize.lua:261-301` | The hot path forwards the JWT payload header and then still performs manual base64 and JSON decoding in Lua, including a second decode of the bearer token to read `aud`. That is duplicated per-request work in the latency-critical path. | The filter chain needs to stop doing redundant JWT parsing work on every request. |

## Audit Blockers

- `hey` is not installed locally, so the exact benchmark command from the spec cannot be run as written.
- The repo does not contain the package-size benchmark fixture set referenced by the spec (`small`, `medium`, `large` tiers under `s3://data-yaml-spec-tests/scale/`), so the requested size sweep cannot be reproduced from the checked-in test data.
- The seeded state in `.rale-seed-state.json` only contains three small package URIs (`alpha/home`, `bio/home`, `compute/home`), which is insufficient for the required package-size comparison.

## Summary

The repo is missing the repeatable performance harness the spec asks for, and the local bootstrap for the Envoy benchmark path is broken. The Lua filter also does avoidable work in the request path, which is the main code-level performance issue visible from static review.
