# Performance Audit Results

Run date: 2026-03-23  
Spec reviewed: `specs/23-audits/03-performance-audit.md`  
Run mode: live-cloud execution against the revised direct-route benchmark flow

## Executive Summary

The updated spec was materially improved because it removed the Terraform auth-toggle from the baseline path and switched the baseline to the dedicated `/_perf/...` direct route. The revised live run completed further than the previous attempt: the direct-route baseline ran, `/token` returned `200`, the authenticated `1k` run completed, the authenticated package sweep completed, Envoy admin stats were collected via ECS exec, and the ALB health check ended at `200`.

The measured request paths did not succeed, though:

- the direct-route baseline for `scale/1k@40ff9e73` returned `404 NoSuchKey` for all `1000` requests
- the authenticated `1k`, `10k`, `100k`, and `1m` package runs all returned `401` responses, even with a control-plane-issued token

## Review Notes

- The updated spec correctly moved the baseline away from the auth-toggle path and onto the dedicated `/_perf/...` route.
- The updated spec still names the ECS container as `envoy`, but the live stats capture was executed against the current running container `EnvoyProxy`.
- The revised flow avoids the previous post-restore `/token` regression, because this run did not rely on toggling `auth_disabled`.

## Preflight

| check | status | body excerpt |
|---|---:|---|
| `GET /health` | `200` | `{"status":"ok"}` |
| `GET /_perf/data-yaml-spec-tests/scale/1k@40ff9e73` | `404` | S3 `NoSuchKey` for key `scale/1k@40ff9e73` |

## Baseline: Direct Route

Target: `http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com/_perf/data-yaml-spec-tests/scale/1k@40ff9e73`

| metric | value |
|---|---|
| total time | `11.3328 s` |
| slowest | `0.2659 s` |
| fastest | `0.0749 s` |
| average | `0.1108 s` |
| requests/sec | `88.2398` |
| p10 | `0.0824 s` |
| p25 | `0.0885 s` |
| p50 | `0.1012 s` |
| p75 | `0.1252 s` |
| p90 | `0.1487 s` |
| p95 | `0.1710 s` |
| p99 | `0.2214 s` |
| status distribution | `404 x 1000` |

## Control-Plane Token Mint

The control-plane request succeeded:

| endpoint | principal | status |
|---|---|---:|
| `POST /token` | `arn:aws:iam::712023778557:user/ernest-staging` | `200` |

Observed response payload fields:

| field | value |
|---|---|
| `principal` | `arn:aws:iam::712023778557:user/ernest-staging` |
| `token` | present |

## Auth-Enabled Benchmark: `scale/1k`

Target: `http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com/data-yaml-spec-tests/scale/1k@40ff9e73`

| metric | value |
|---|---|
| total time | `10.1518 s` |
| slowest | `0.3165 s` |
| fastest | `0.0692 s` |
| average | `0.1009 s` |
| requests/sec | `98.5047` |
| total data | `28000 bytes` |
| size/request | `28 bytes` |
| p10 | `0.0750 s` |
| p25 | `0.0800 s` |
| p50 | `0.0912 s` |
| p75 | `0.1143 s` |
| p90 | `0.1343 s` |
| p95 | `0.1596 s` |
| p99 | `0.2242 s` |
| status distribution | `401 x 1000` |

## Package Size Variation

### `scale/10k@e75c5d5e`

| metric | value |
|---|---|
| total time | `2.5988 s` |
| slowest | `0.4003 s` |
| fastest | `0.0699 s` |
| average | `0.1168 s` |
| requests/sec | `76.9582` |
| total data | `5600 bytes` |
| size/request | `28 bytes` |
| p10 | `0.0756 s` |
| p25 | `0.0893 s` |
| p50 | `0.1058 s` |
| p75 | `0.1376 s` |
| p90 | `0.1660 s` |
| p95 | `0.1870 s` |
| p99 | `0.3708 s` |
| status distribution | `401 x 200` |

### `scale/100k@eb6c8db9`

| metric | value |
|---|---|
| total time | `2.3368 s` |
| slowest | `0.2364 s` |
| fastest | `0.0725 s` |
| average | `0.1113 s` |
| requests/sec | `85.5886` |
| total data | `5600 bytes` |
| size/request | `28 bytes` |
| p10 | `0.0800 s` |
| p25 | `0.0856 s` |
| p50 | `0.1002 s` |
| p75 | `0.1343 s` |
| p90 | `0.1473 s` |
| p95 | `0.1965 s` |
| p99 | `0.2362 s` |
| status distribution | `401 x 200` |

### `scale/1m@2a5a6715`

| metric | value |
|---|---|
| total time | `3.4767 s` |
| slowest | `0.8403 s` |
| fastest | `0.0701 s` |
| average | `0.1512 s` |
| requests/sec | `57.5256` |
| total data | `5600 bytes` |
| size/request | `28 bytes` |
| p10 | `0.0807 s` |
| p25 | `0.0944 s` |
| p50 | `0.1209 s` |
| p75 | `0.1445 s` |
| p90 | `0.2319 s` |
| p95 | `0.4609 s` |
| p99 | `0.7973 s` |
| status distribution | `401 x 200` |

## Envoy Admin Stats

ECS exec succeeded against the running Envoy container. The captured output included a valid Session Manager session banner and Envoy stats output.

Requested metric grep result from the captured file:

| pattern set | matched lines |
|---|---:|
| `jwt_authn|lua|downstream.*_ms|upstream_rq_time|upstream_rq_401|upstream_rq_404|downstream_rq` | `1` |

Matched line:

| stat |
|---|
| `cluster.ec2_instance_metadata_server_internal.upstream_rq_timeout: 0` |

## Final Health

| check | status | body |
|---|---:|---|
| `GET /health` | `200` | `{"status":"ok"}` |

## Artifacts Collected

Temporary run artifacts were written under `/tmp/raja-perf-20260324/`:

- `preflight.json`
- `baseline-direct-1k.txt`
- `token-response.json`
- `auth-1k.txt`
- `auth-10k.txt`
- `auth-100k.txt`
- `auth-1m.txt`
- `envoy-stats.txt`
- `final-health.json`
