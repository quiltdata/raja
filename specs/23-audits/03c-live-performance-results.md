# Performance Audit Results

Run date: 2026-03-23  
Spec reviewed: `specs/23-audits/03-performance-audit.md`  
Run mode: live-cloud execution against the deployed stack

## Executive Summary

The updated spec was executable, but the live run did not complete end-to-end. The auth-disabled baseline completed and produced latency data for `scale/1k@40ff9e73`. After auth was re-enabled, the benchmark stopped because the control-plane `/token` endpoint regressed to `404 Principal not found` for the seeded default principal, so the authenticated benchmark and package-size sweep could not proceed.

## Review Notes

- The updated spec is materially closer to the live stack than the prior version because it now documents the prerequisite seeding and Terraform setup required for `data-yaml-spec-tests`.
- The run still exposed live-state instability after the auth toggle. Before the benchmark sequence, `python scripts/verify_perf_access.py` passed end-to-end; after the auth-disabled baseline and auth restore, `/token` returned `404 Principal not found` again for `arn:aws:iam::712023778557:user/ernest-staging`.
- During the benchmark sequence, the ALB `/health` endpoint returned `200` both after disabling auth and after re-enabling auth. A later direct `/health` check after the failed run returned `301 PermanentRedirect` with an S3-style bucket redirect response for `health`.

## Steps Executed

1. Ran `python scripts/verify_perf_access.py` before starting the benchmark.
2. Applied `terraform apply -var auth_disabled=true` in `infra/terraform/`.
3. Waited for ALB `/health` to return `200`.
4. Ran the auth-disabled baseline benchmark for `scale/1k@40ff9e73`.
5. Applied `terraform apply -var auth_disabled=false` in `infra/terraform/`.
6. Waited for ALB `/health` to return `200`.
7. Attempted to mint the authenticated benchmark token via `POST /token`.
8. Stopped when `/token` returned `404 Principal not found`.

## Observed Results

### Preflight

`python scripts/verify_perf_access.py` passed before the benchmark sequence began:

- `/token` → `200`
- Envoy GET `/data-yaml-spec-tests/scale/1k@40ff9e73` → `200`
- ECS `execute-command` → `200`

### Health During Rollout

Captured health checks during the Terraform toggles:

| checkpoint | status | body |
|---|---:|---|
| after `auth_disabled=true` | `200` | `{"status":"ok"}` |
| after `auth_disabled=false` | `200` | `{"status":"ok"}` |

### Baseline: Auth Disabled

Target: `http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com/data-yaml-spec-tests/scale/1k@40ff9e73`

| metric | value |
|---|---|
| total time | `15.1463 s` |
| slowest | `2.8343 s` |
| fastest | `0.0849 s` |
| average | `0.1487 s` |
| requests/sec | `66.0229` |
| total data | `60000 bytes` |
| size/request | `60 bytes` |
| p10 | `0.0957 s` |
| p25 | `0.1033 s` |
| p50 | `0.1151 s` |
| p75 | `0.1443 s` |
| p90 | `0.1650 s` |
| p95 | `0.1803 s` |
| p99 | `2.3239 s` |
| status distribution | `403 x 1000` |

### Auth-Enabled Token Mint Attempt

After re-enabling auth, the live control-plane request returned:

| endpoint | principal | status | body |
|---|---|---:|---|
| `POST /token` | `arn:aws:iam::712023778557:user/ernest-staging` | `404` | `{"detail":"Principal not found: arn:aws:iam::712023778557:user/ernest-staging"}` |

### Post-Run Direct Health Probe

The direct `/health` probe after the failed run returned:

| endpoint | status | body excerpt |
|---|---:|---|
| `GET /health` | `301` | S3-style `PermanentRedirect` response naming bucket `health` |

## Blocked Steps

The following spec steps were not completed because `/token` failed after auth was restored:

- Auth-enabled `scale/1k@40ff9e73` benchmark
- Auth-enabled package sweep:
  - `scale/10k@e75c5d5e`
  - `scale/100k@eb6c8db9`
  - `scale/1m@2a5a6715`
- Envoy admin stats capture for the benchmark run

## Artifacts Collected

Temporary run artifacts were written under `/tmp/raja-perf-20260323/`:

- `baseline-1k.txt`
- `baseline-health-after-disable.json`
- `health-after-enable.json`
- `terraform-auth-disabled.txt`
- `terraform-auth-enabled.txt`
