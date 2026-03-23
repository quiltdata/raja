# Performance Audit — RAJA JWT+Lua Filter Chain

## Objective

Quantify the latency overhead introduced by the Envoy JWT+Lua authorization filter chain
relative to an unauthenticated baseline for S3 object streaming. Establish per-percentile
baselines (P50/P95/P99), isolate the auth cost, and determine whether optimization
(caching, native Envoy filter) is required before production.

## Scope

| In | Out |
|----|-----|
| Envoy JWT+Lua filter chain latency | DataZone subscription grant resolution time |
| S3 object streaming throughput across package sizes | Lambda cold-start optimization |
| A/B comparison: auth enabled vs disabled | API Gateway latency |
| | Network egress cost |
| | CI performance regression gate |
| | Infrastructure right-sizing |

## Prerequisites

### `hey` — HTTP Load Generator

[`hey`](https://github.com/rakyll/hey) is a Go-based HTTP benchmarking tool used throughout
this spec to drive load and collect latency percentiles.

```bash
# macOS
brew install hey

# Linux (or any platform with Go installed)
go install github.com/rakyll/hey@latest
```

Basic usage: `hey -n <total-requests> -c <concurrency> [flags] <url>`

---

## Approach

### 1. Establish Baseline: Auth-Disabled Envoy

Toggle the live stack to disable the JWT+Lua filter chain via the `auth_disabled` Terraform
variable. This sets `AUTH_DISABLED=true` in the ECS task, which causes `entrypoint.sh` to
emit an empty `__AUTH_FILTER__` block. Re-deploy, wait for ECS to stabilize, then run:

```bash
# Disable auth on the live stack — NEVER leave this in place
cd infra/terraform && terraform apply -var auth_disabled=true

ENVOY=http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com

hey -n 1000 -c 10 -m GET \
  -H "x-test-run: baseline-no-auth" \
  "$ENVOY/b/data-yaml-spec-tests/packages/scale/1k@40ff9e73"

# Re-enable auth immediately after
terraform apply -var auth_disabled=false
```

Collect: mean, P50, P95, P99 latency; requests/sec; error rate.

### 2. Auth-Enabled Benchmark (JWT+Lua Active)

Re-run identical requests with the JWT+Lua filter chain fully enabled. Mint a `raja` token
via the RALE control plane API to eliminate token issuance latency from measurements.

```bash
API=https://wezevk884h.execute-api.us-east-1.amazonaws.com/prod
ENVOY=http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com

# Mint a long-lived test token via the control plane (test env only — never production)
# Principal from .rale-seed-state.json default_principal
ADMIN_KEY=$(grep RAJA_ADMIN_KEY .env | cut -d= -f2)
TOKEN=$(curl -s -X POST "$API/token" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{"principal":"arn:aws:iam::712023778557:user/ernest-staging","token_type":"raja","ttl":3600}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

hey -n 1000 -c 10 -m GET \
  -H "Authorization: Bearer $TOKEN" \
  -H "x-test-run: auth-enabled" \
  "$ENVOY/b/data-yaml-spec-tests/packages/scale/1k@40ff9e73"
```

### 3. Package Size Variation

Scale fixture packages exist in `s3://data-yaml-spec-tests` (created via the Quilt
Packaging Engine). Each package covers one size tier:

| Package | Object count | Purpose | Hash |
| ------- | ------------ | ------- | ---- |
| `scale/1k` | ~1,000 files | Latency floor | `40ff9e73` |
| `scale/10k` | ~10,000 files | Moderate load | `e75c5d5e` |
| `scale/100k` | ~100,000 files | Heavy load | `eb6c8db9` |
| `scale/1m` | ~1,000,000 files | Throughput ceiling | `2a5a6715` |

Browse at: `https://nightly.quilttest.com/b/data-yaml-spec-tests/packages/scale/`

Mint a token (same method as Step 2), then run `hey` against each tier and record
P50/P95/P99 latency:

```bash
API=https://wezevk884h.execute-api.us-east-1.amazonaws.com/prod
ENVOY=http://raja-standalone-rajee-alb-2076392115.us-east-1.elb.amazonaws.com
ADMIN_KEY=$(grep RAJA_ADMIN_KEY .env | cut -d= -f2)
TOKEN=$(curl -s -X POST "$API/token" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ADMIN_KEY" \
  -d '{"principal":"arn:aws:iam::712023778557:user/ernest-staging","token_type":"raja","ttl":3600}' \
  | python3 -c "import sys,json; print(json.load(sys.stdin)['token'])")

declare -A HASHES=([1k]=40ff9e73 [10k]=e75c5d5e [100k]=eb6c8db9 [1m]=2a5a6715)
for PKG in 1k 10k 100k 1m; do
  echo "=== scale/$PKG ==="
  hey -n 200 -c 10 -m GET \
    -H "Authorization: Bearer $TOKEN" \
    -H "x-test-run: perf-$PKG" \
    "$ENVOY/b/data-yaml-spec-tests/packages/scale/${PKG}@${HASHES[$PKG]}"
done
```

### 4. Envoy Admin Stats

The admin port (9901) is only exposed via the ALB if `admin_allowed_cidrs` is set in
Terraform. Access it via ECS exec instead:

```bash
# Get the running task ARN
TASK=$(aws ecs list-tasks \
  --cluster raja-standalone-rajee-cluster \
  --service-name raja-standalone-rajee-service \
  --query 'taskArns[0]' --output text)

# Collect filter chain stats from the live container
aws ecs execute-command \
  --cluster raja-standalone-rajee-cluster \
  --task "$TASK" \
  --container envoy \
  --interactive \
  --command "curl -s http://localhost:9901/stats" \
  | grep -E "(http.*.jwt_authn|lua|downstream_cx_length_ms|upstream_rq_time)"
```

Key metrics to record: `envoy_http_jwt_authn_allowed`, `envoy_http_jwt_authn_denied`,
`lua.errors`, `downstream_rq_time` histograms.

### 5. Document Results and Optimization Decision

After collecting data, populate `docs/performance.md` with:

- Baseline vs auth-enabled latency table (P50/P95/P99 by package size)
- Calculated auth overhead % = `(auth_p99 - baseline_p99) / baseline_p99 * 100`
- Throughput comparison (MB/s) per size tier

**Optimization trigger:** If auth overhead > 15% at P99 for any size tier, evaluate:
1. **Lua-side JWT caching** — cache decoded token in Envoy shared data keyed on
   `Authorization` header hash; invalidate on expiry
2. **Native `jwt_authn` HTTP filter** — replace Lua JWT decode with Envoy's built-in
   `envoy.filters.http.jwt_authn` filter, which is implemented in C++ and significantly
   faster than Lua for cryptographic operations
3. **Connection-level auth** — consider moving scope validation to the RALE Authorizer
   response and trusting Envoy's built-in JWT verification for the hot path

### 6. Record Baseline for Future Regression Gate

Once results are in `docs/performance.md`, the P99 numbers become the baseline for a
future CI gate. That is a separate task — do not add CI changes as part of this audit.

## Deliverables

1. **`docs/performance.md`** — baseline vs auth latency tables, overhead %, optimization
   recommendation with rationale
2. **Envoy stats snapshot** (`docs/audits/envoy-stats-baseline.txt`) captured during
   benchmark run for future regression comparison

## Success Criteria

| Metric | Target |
|--------|--------|
| Auth overhead at P99 (`scale/1k`) | Measured and documented |
| Auth overhead at P99 (`scale/10k`) | Measured and documented |
| Auth overhead > 15% P99 | Optimization plan filed as GitHub issue |
| `docs/performance.md` published | Yes, with raw numbers |
| Baseline stats snapshot committed | Yes |
