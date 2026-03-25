# Performance Audit Results

Run date: 2026-03-23  
Spec reviewed: `specs/23-audits/03-performance-audit.md`  
Run mode: live-cloud execution on real file-read paths

## Executive Summary

The benchmark now targets actual object reads rather than package references:

- direct baseline: AWS CLI reads `s3://data-yaml-spec-tests/scale/1k/e2-0/e1-0/e0-0.txt`
- authenticated path: Envoy reads `.../scale/<pkg>@<hash>/<logical_key>`

With that correction in place:

- the direct S3 baseline succeeded
- the authenticated file-read path succeeded for all four package sizes
- `1k` had some `503` responses under load
- `10k`, `100k`, and `1m` completed with `200` responses only

## Targets Used

| tier | direct object key | authenticated URL suffix |
|---|---|---|
| `1k` | `scale/1k/e2-0/e1-0/e0-0.txt` | `scale/1k@40ff9e73/e2-0/e1-0/e0-0.txt` |
| `10k` | `scale/10k/e3-0/e2-0/e1-0/e0-0.txt` | `scale/10k@e75c5d5e/e3-0/e2-0/e1-0/e0-0.txt` |
| `100k` | `scale/100k/e4-0/e3-0/e2-0/e1-0/e0-0.txt` | `scale/100k@eb6c8db9/e4-0/e3-0/e2-0/e1-0/e0-0.txt` |
| `1m` | `scale/1m/e0/e4-0/e3-0/e2-0/e1-0/e0-0.txt` | `scale/1m@2a5a6715/e0/e4-0/e3-0/e2-0/e1-0/e0-0.txt` |

## Preflight

| check | status | body |
|---|---:|---|
| `GET /health` | `200` | `{"status":"ok"}` |

## Direct S3 Baseline

Method: `aws s3 cp s3://data-yaml-spec-tests/scale/1k/e2-0/e1-0/e0-0.txt -` repeated `100` times

| metric | value |
|---|---|
| sample count | `100` |
| average | `0.9913 s` |
| p50 | `0.9237 s` |
| p95 | `1.0955 s` |
| p99 | `7.1055 s` |

## Control-Plane Token Mint

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

Target suffix: `scale/1k@40ff9e73/e2-0/e1-0/e0-0.txt`

| metric | value |
|---|---|
| total time | `132.3080 s` |
| slowest | `10.7101 s` |
| fastest | `0.0769 s` |
| average | `1.2171 s` |
| requests/sec | `7.5581` |
| total data | `538438 bytes` |
| size/request | `538 bytes` |
| p10 | `0.8051 s` |
| p25 | `0.8501 s` |
| p50 | `0.9149 s` |
| p75 | `1.1508 s` |
| p90 | `1.8394 s` |
| p95 | `2.8622 s` |
| p99 | `5.5608 s` |
| status distribution | `200 x 979`, `503 x 21` |

## Package Size Variation

### `scale/10k@e75c5d5e/e3-0/e2-0/e1-0/e0-0.txt`

| metric | value |
|---|---|
| total time | `27.9575 s` |
| slowest | `5.8393 s` |
| fastest | `0.7550 s` |
| average | `1.1873 s` |
| requests/sec | `7.1537` |
| total data | `110400 bytes` |
| size/request | `552 bytes` |
| p10 | `0.8149 s` |
| p25 | `0.8440 s` |
| p50 | `0.9166 s` |
| p75 | `1.1027 s` |
| p90 | `1.8855 s` |
| p95 | `3.2802 s` |
| p99 | `4.8343 s` |
| status distribution | `200 x 200` |

### `scale/100k@eb6c8db9/e4-0/e3-0/e2-0/e1-0/e0-0.txt`

| metric | value |
|---|---|
| total time | `30.3672 s` |
| slowest | `10.2837 s` |
| fastest | `0.7611 s` |
| average | `1.2044 s` |
| requests/sec | `6.5861` |
| total data | `111000 bytes` |
| size/request | `555 bytes` |
| p10 | `0.8036 s` |
| p25 | `0.8443 s` |
| p50 | `0.8965 s` |
| p75 | `0.9978 s` |
| p90 | `1.6620 s` |
| p95 | `2.7907 s` |
| p99 | `9.0632 s` |
| status distribution | `200 x 200` |

### `scale/1m@2a5a6715/e0/e4-0/e3-0/e2-0/e1-0/e0-0.txt`

| metric | value |
|---|---|
| total time | `32.3333 s` |
| slowest | `13.1294 s` |
| fastest | `0.7790 s` |
| average | `1.1978 s` |
| requests/sec | `6.1856` |
| total data | `109600 bytes` |
| size/request | `548 bytes` |
| p10 | `0.8234 s` |
| p25 | `0.8601 s` |
| p50 | `0.9010 s` |
| p75 | `0.9854 s` |
| p90 | `1.3962 s` |
| p95 | `2.0509 s` |
| p99 | `9.3205 s` |
| status distribution | `200 x 200` |

## Envoy Admin Stats

ECS exec succeeded against the running Envoy container.

Grep result for the requested stat patterns:

| pattern set | matched lines |
|---|---:|
| `jwt_authn|lua|downstream_cx_length_ms|upstream_rq_time|downstream_rq|upstream_rq_503|upstream_rq_5xx` | `5` |

Matched lines:

| stat |
|---|
| `cluster.ec2_instance_metadata_server_internal.internal.upstream_rq_503: 602` |
| `cluster.ec2_instance_metadata_server_internal.internal.upstream_rq_5xx: 602` |
| `cluster.ec2_instance_metadata_server_internal.upstream_rq_503: 602` |
| `cluster.ec2_instance_metadata_server_internal.upstream_rq_5xx: 602` |
| `cluster.ec2_instance_metadata_server_internal.upstream_rq_timeout: 0` |

## Final Health

| check | status | body |
|---|---:|---|
| `GET /health` | `200` | `{"status":"ok"}` |

## Artifacts Collected

Temporary run artifacts were written under `/tmp/raja-perf-20260324c/`:

- `preflight-and-baseline.json`
- `token-response.json`
- `auth-1k.txt`
- `auth-10k.txt`
- `auth-100k.txt`
- `auth-1m.txt`
- `envoy-stats.txt`
- `final-health.json`
