# Performance Audit Results

Run date: 2026-03-23  
Run mode: live-cloud only  
Target endpoint: `http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com`

This file records only observed run data and run blockers from the live benchmark attempt.

## Commands Executed

1. Live baseline with auth disabled:
   `terraform apply -var auth_disabled=true`
   `hey -n 1000 -c 10 -m GET http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com/b/data-yaml-spec-tests/packages/scale/1k@40ff9e73`
2. Live auth restore:
   `terraform apply -var auth_disabled=false`
3. Live control-plane token mint attempt:
   `POST https://wezevk884h.execute-api.us-east-1.amazonaws.com/prod/token`
4. Live authenticated probe with manually minted JWT:
   `GET http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com/b/data-yaml-spec-tests/packages/scale/1k@40ff9e73`
5. Live denied-path benchmark with manually minted JWT:
   `hey -n 200 -c 10 -m GET .../b/data-yaml-spec-tests/packages/scale/1k@40ff9e73`
6. Live Envoy stats attempt via ECS exec:
   `aws ecs execute-command ... --command "curl -s http://localhost:9901/stats"`

## Observed Results

### Baseline: Auth Disabled

Package: `scale/1k@40ff9e73`  
Start: `2026-03-23T21:36:30Z`

| metric | value |
|---|---|
| total requests | `1000` |
| concurrency | `10` |
| total time | `12.2305 s` |
| average | `0.1188 s` |
| fastest | `0.0759 s` |
| slowest | `0.3497 s` |
| requests/sec | `81.7627` |
| p50 | `0.1086 s` |
| p95 | `0.1785 s` |
| p99 | `0.3143 s` |
| status distribution | `400 x 1000` |

### Control-Plane Token Mint

Endpoint: `POST https://wezevk884h.execute-api.us-east-1.amazonaws.com/prod/token`

Observed responses for seeded principals:

| principal | status | body excerpt |
|---|---:|---|
| `arn:aws:iam::712023778557:user/ernest-staging` | `404` | `{"detail":"Principal not found: arn:aws:iam::712023778557:user/ernest-staging"}` |
| `arn:aws:iam::712023778557:user/simon-staging` | `404` | `{"detail":"Principal not found: arn:aws:iam::712023778557:user/simon-staging"}` |
| `arn:aws:iam::712023778557:user/kevin-staging` | `404` | `{"detail":"Principal not found: arn:aws:iam::712023778557:user/kevin-staging"}` |
| `arn:aws:iam::712023778557:user/sergey` | `404` | `{"detail":"Principal not found: arn:aws:iam::712023778557:user/sergey"}` |

### Manual Authenticated Probe

JWT source: manually minted HS256 token using the live signing secret, issuer `https://wezevk884h.execute-api.us-east-1.amazonaws.com`, audience `raja-s3-proxy`, subject `arn:aws:iam::712023778557:user/ernest-staging`

| request | status | body |
|---|---:|---|
| `GET /b/data-yaml-spec-tests/packages/scale/1k@40ff9e73` | `403` | `{"decision": "DENY", "error": "principal project not found"}` |

### Denied Auth Path Benchmark

Package: `scale/1k@40ff9e73`  
Start: `2026-03-23T21:41:51Z`

| metric | value |
|---|---|
| total requests | `200` |
| concurrency | `10` |
| total time | `5.3166 s` |
| average | `0.2392 s` |
| fastest | `0.0907 s` |
| slowest | `2.7520 s` |
| requests/sec | `37.6177` |
| p50 | `0.1329 s` |
| p95 | `0.4923 s` |
| p99 | `2.5284 s` |
| total data | `12000 bytes` |
| size/request | `60 bytes` |
| status distribution | `403 x 200` |

## Blocked Steps

### Auth-Enabled Authorized Benchmark

The spec’s authenticated authorized path could not be completed with a live control-plane-issued token because every tested seeded principal returned `404 Principal not found` from `/token`.

### Package Size Matrix (`1k`, `10k`, `100k`, `1m`)

The full authorized package matrix was not run because the authenticated authorized setup did not succeed. No live successful request-path measurements were collected for:

- `scale/10k@e75c5d5e`
- `scale/100k@eb6c8db9`
- `scale/1m@2a5a6715`

### Envoy Admin Stats Snapshot

The ECS exec step returned:

`InvalidParameterException: The execute command failed because execute command was not enabled when the task was run or the execute command agent isn't running.`

No live `jwt_authn` / `lua` stats snapshot was collected.

## Raw Outcome Summary

| phase | result |
|---|---|
| baseline live request path | completed |
| baseline response status | `400` for all requests |
| auth restore | completed |
| live control-plane token mint | blocked by `404 Principal not found` |
| manual authenticated probe | completed |
| manual authenticated response status | `403 principal project not found` |
| denied-path authenticated benchmark | completed |
| Envoy admin stats collection | blocked by ECS exec configuration |
