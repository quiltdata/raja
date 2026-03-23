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
| Local echo-server isolation benchmark | Network egress cost |
| CI performance regression gate | Infrastructure right-sizing |

## Approach

### 1. Establish Baseline: Auth-Disabled Envoy

Deploy Envoy with the JWT+Lua filter chain disabled (or bypassed via `per_filter_config`)
against the existing integration test infrastructure in `tests/integration/`.

```bash
# Use existing infra from tf-outputs.json; override Envoy config for baseline run
# Set failure_mode_allow: true and remove jwt_authn filter for baseline only
# NEVER commit this config; it is test-only

hey -n 1000 -c 10 -m GET \
  -H "x-test-run: baseline-no-auth" \
  https://<envoy-endpoint>/b/<bucket>/packages/<pkg>@<hash>/<object>
```

Collect: mean, P50, P95, P99 latency; requests/sec; error rate.

### 2. Auth-Enabled Benchmark (JWT+Lua Active)

Re-run identical requests with the JWT+Lua filter chain fully enabled. Use a pre-minted
valid TAJ token with appropriate scopes to eliminate token issuance latency from
measurements.

```bash
# Mint a long-lived test token (test env only — never production)
TOKEN=$(python -m raja token mint --scopes "s3:read" --ttl 3600)

hey -n 1000 -c 10 -m GET \
  -H "Authorization: Bearer $TOKEN" \
  -H "x-test-run: auth-enabled" \
  https://<envoy-endpoint>/b/<bucket>/packages/<pkg>@<hash>/<object>
```

### 3. Isolate Auth Cost: Echo Server Upstream

Replace the real S3/Lambda upstream with a local echo server to eliminate upstream
variability from measurements. This isolates filter chain overhead from network and S3 latency.

```yaml
# docker-compose.local.yml addition: add echo upstream service
services:
  echo:
    image: mendhak/http-https-echo:latest
    ports: ["8081:8080"]
```

Run the same A/B test (auth off vs auth on) against the echo upstream. The delta between
the two runs is the pure Envoy JWT+Lua overhead.

```bash
# Baseline vs auth against echo server — isolates filter overhead
hey -n 5000 -c 20 http://localhost:9901/echo  # envoy → echo, no auth
hey -n 5000 -c 20 -H "Authorization: Bearer $TOKEN" \
  http://localhost:9901/echo                   # envoy → echo, auth enabled
```

### 4. Package Size Variation

Test against packages at each scale tier under
`s3://data-yaml-spec-tests/scale/`:

| Folder | Approx. size | Purpose |
|--------|-------------|---------|
| `small/` | < 1 MB | Latency floor |
| `medium/` | 10–100 MB | Typical use |
| `large/` | > 500 MB | Throughput ceiling |

For each size tier, record throughput (MB/s) and P99 latency with auth enabled.
Use the existing `tests/integration/helpers.py` fixture patterns to enumerate packages.

```python
# tests/integration/test_perf_filter_chain.py (new file)
import pytest, statistics, time, requests

SCALE_PACKAGES = [
    ("small", "data-yaml-spec-tests", "scale/small/..."),
    ("medium", "data-yaml-spec-tests", "scale/medium/..."),
    ("large", "data-yaml-spec-tests", "scale/large/..."),
]

@pytest.mark.performance
@pytest.mark.parametrize("label,bucket,key", SCALE_PACKAGES)
def test_latency_by_package_size(label, bucket, key, auth_token, envoy_url):
    latencies = []
    for _ in range(50):
        t0 = time.perf_counter()
        r = requests.get(f"{envoy_url}/b/{bucket}/{key}",
                         headers={"Authorization": f"Bearer {auth_token}"})
        latencies.append(time.perf_counter() - t0)
        assert r.status_code == 200
    p99 = statistics.quantiles(latencies, n=100)[98]
    assert p99 < 2.0, f"{label} P99 {p99:.3f}s exceeds 2s threshold"
```

### 5. Envoy Admin Stats

Use the Envoy admin API to collect internal filter chain metrics:

```bash
# Per-filter timing breakdown
curl http://localhost:9901/stats | grep -E \
  "(http.*.jwt_authn|lua|downstream_cx_length_ms|upstream_rq_time)"

# Watch live during load test
watch -n1 'curl -s http://localhost:9901/stats/prometheus | \
  grep envoy_http_downstream_rq_time'
```

Key metrics to record: `envoy_http_jwt_authn_allowed`, `envoy_http_jwt_authn_denied`,
`lua.errors`, `downstream_rq_time` histograms.

### 6. Document Results and Optimization Decision

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

### 7. CI Performance Regression Gate

Add a `performance` pytest marker and a GitHub Actions workflow step:

```yaml
# .github/workflows/ci.yml addition
- name: Performance regression check
  if: github.event_name == 'pull_request'
  run: |
    pytest -m performance tests/integration/test_perf_filter_chain.py \
      --tb=short -q
  env:
    RAJA_ENVOY_URL: ${{ secrets.NIGHTLY_ENVOY_URL }}
    RAJA_TEST_TOKEN: ${{ secrets.NIGHTLY_TEST_TOKEN }}
```

Gate: P99 latency for `small` package must be < 500 ms; `medium` < 2 s.

## Deliverables

1. **`docs/performance.md`** — baseline vs auth latency tables, overhead %, optimization
   recommendation with rationale
2. **`tests/integration/test_perf_filter_chain.py`** — parametrized performance test suite
   with `@pytest.mark.performance` marker
3. **CI step** in `.github/workflows/ci.yml` gating on P99 thresholds
4. **Envoy stats snapshot** (`docs/audits/envoy-stats-baseline.txt`) captured during
   benchmark run for future regression comparison

## Success Criteria

| Metric | Target |
|--------|--------|
| Auth overhead at P99 (small package) | Measured and documented |
| Auth overhead at P99 (medium package) | Measured and documented |
| Auth overhead > 15% P99 | Optimization plan filed as GitHub issue |
| `test_perf_filter_chain.py` passing in CI | Yes |
| `docs/performance.md` published | Yes, with raw numbers |
| Baseline stats snapshot committed | Yes |
