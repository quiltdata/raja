# Performance Audit Results

Run date: 2026-03-23  
Spec reviewed: `specs/23-audits/03-performance-audit.md`  
Run mode: live-cloud execution after aligning the verifier with the updated spec

## Executive Summary

The verifier and spec now align on the two important paths that had previously diverged:

- the direct-route baseline checks the exact benchmark path `/_perf/data-yaml-spec-tests/scale/1k@40ff9e73`
- the authenticated benchmark uses a `token_type="rajee"` token, which carries the issuer and audience claims Envoy expects

With those aligned, the live run showed two distinct behaviors:

- the direct-route baseline still returns `404 NoSuchKey` for the benchmark package path
- the authenticated benchmark path now succeeds and returns mostly `200` responses across `1k`, `10k`, `100k`, and `1m`

## Review Notes

- The updated spec’s auth token path now matches the verifier and the live Envoy JWT configuration.
- The direct-route baseline still points at a package path that S3 reports as missing through the `/_perf/...` route.
- The live Envoy stats capture still did not yield the `jwt_authn` / `lua` metric names requested by the spec when grepped from the captured output.

## Preflight

| check | status | body excerpt |
|---|---:|---|
| `GET /health` | `200` | `{"status":"ok"}` |
| `GET /_perf/data-yaml-spec-tests/scale/1k@40ff9e73` | `404` | S3 `NoSuchKey` for key `scale/1k@40ff9e73` |

## Baseline: Direct Route

Target: `http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com/_perf/data-yaml-spec-tests/scale/1k@40ff9e73`

| metric | value |
|---|---|
| total time | `11.8120 s` |
| slowest | `0.4831 s` |
| fastest | `0.0806 s` |
| average | `0.1131 s` |
| requests/sec | `84.6593` |
| p10 | `0.0894 s` |
| p25 | `0.0936 s` |
| p50 | `0.1002 s` |
| p75 | `0.1216 s` |
| p90 | `0.1550 s` |
| p95 | `0.1766 s` |
| p99 | `0.2466 s` |
| status distribution | `404 x 1000` |

## Control-Plane Token Mint

The control-plane token request succeeded:

| endpoint | principal | status |
|---|---|---:|
| `POST /token` | `arn:aws:iam::712023778557:user/ernest-staging` | `200` |

Observed token payload claims:

| claim | value |
|---|---|
| `sub` | `arn:aws:iam::712023778557:user/ernest-staging` |
| `iss` | `https://wezevk884h.execute-api.us-east-1.amazonaws.com` |
| `aud` | `["raja-s3-proxy"]` |
| `scopes` | `[]` |

## Auth-Enabled Benchmark: `scale/1k`

Target: `http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com/data-yaml-spec-tests/scale/1k@40ff9e73`

| metric | value |
|---|---|
| total time | `135.9256 s` |
| slowest | `10.9595 s` |
| fastest | `0.7409 s` |
| average | `1.2386 s` |
| requests/sec | `7.3570` |
| total data | `547498 bytes` |
| size/request | `547 bytes` |
| p10 | `0.8047 s` |
| p25 | `0.8351 s` |
| p50 | `0.8943 s` |
| p75 | `1.1752 s` |
| p90 | `1.8651 s` |
| p95 | `2.9228 s` |
| p99 | `5.5606 s` |
| status distribution | `200 x 999`, `503 x 1` |

## Package Size Variation

### `scale/10k@e75c5d5e`

| metric | value |
|---|---|
| total time | `30.6284 s` |
| slowest | `8.6047 s` |
| fastest | `0.7813 s` |
| average | `1.2573 s` |
| requests/sec | `6.5299` |
| total data | `109894 bytes` |
| size/request | `549 bytes` |
| p10 | `0.8222 s` |
| p25 | `0.8635 s` |
| p50 | `0.9269 s` |
| p75 | `1.2376 s` |
| p90 | `1.7762 s` |
| p95 | `2.7985 s` |
| p99 | `6.7787 s` |
| status distribution | `200 x 199`, `503 x 1` |

### `scale/100k@eb6c8db9`

| metric | value |
|---|---|
| total time | `28.6157 s` |
| slowest | `8.2155 s` |
| fastest | `0.7349 s` |
| average | `1.1429 s` |
| requests/sec | `6.9892` |
| total data | `111000 bytes` |
| size/request | `555 bytes` |
| p10 | `0.8200 s` |
| p25 | `0.8451 s` |
| p50 | `0.8847 s` |
| p75 | `0.9653 s` |
| p90 | `1.7660 s` |
| p95 | `2.7698 s` |
| p99 | `6.2987 s` |
| status distribution | `200 x 200` |

### `scale/1m@2a5a6715`

| metric | value |
|---|---|
| total time | `29.8957 s` |
| slowest | `11.9686 s` |
| fastest | `0.7617 s` |
| average | `1.2608 s` |
| requests/sec | `6.6899` |
| total data | `109600 bytes` |
| size/request | `548 bytes` |
| p10 | `0.8100 s` |
| p25 | `0.8494 s` |
| p50 | `0.9242 s` |
| p75 | `1.1351 s` |
| p90 | `1.8174 s` |
| p95 | `2.6644 s` |
| p99 | `6.5356 s` |
| status distribution | `200 x 200` |

## Envoy Admin Stats

ECS exec succeeded against the running Envoy container.

Grep result for the requested stat patterns:

| pattern set | matched lines |
|---|---:|
| `jwt_authn|lua|downstream_cx_length_ms|upstream_rq_time|downstream_rq|upstream_rq_503|upstream_rq_5xx` | `2` |

Matched lines:

| stat |
|---|
| `cluster.ec2_instance_metadata_server_internal.internal.upstream_rq_503: 484` |
| `cluster.ec2_instance_metadata_server_internal.internal.upstream_rq_5xx: 484` |

## Final Health

| check | status | body |
|---|---:|---|
| `GET /health` | `200` | `{"status":"ok"}` |

## Artifacts Collected

Temporary run artifacts were written under `/tmp/raja-perf-20260324b/`:

- `preflight.json`
- `baseline-direct-1k.txt`
- `token-response.json`
- `auth-1k.txt`
- `auth-10k.txt`
- `auth-100k.txt`
- `auth-1m.txt`
- `envoy-stats.txt`
- `final-health.json`
