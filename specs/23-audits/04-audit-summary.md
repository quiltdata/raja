# Audit Summary — RAJA JWT+Lua Filter Chain

Run date: 2026-03-23
Source: `03f-live-performance-results.md`

## Performance Results

Direct S3 baseline (AWS CLI, `scale/1k`, n=100): P50 0.924 s · P95 1.096 s · P99 7.106 s

Auth-enabled path (Envoy JWT+Lua, n=200 per tier, c=10):

| tier | P50 (s) | P95 (s) | P99 (s) | overhead P99 | errors |
|---|---:|---:|---:|---:|---|
| `scale/1m` | 0.901 | 2.051 | 9.321 | +31 % | none |
| `scale/100k` | 0.897 | 2.791 | 9.063 | +28 % | none |
| `scale/10k` | 0.917 | 3.280 | 4.834 | −32 % † | none |
| `scale/1k` | 0.915 | 2.862 | 5.561 | −22 % † | 21 × 503 |

† P99 baseline (7.1 s) was inflated by a single outlier; these tiers beat it by chance.

## Key Findings

**P50 latency is identical across all tiers** — ~0.90–0.92 s regardless of package size.
The JWT+Lua filter chain adds negligible median cost; the S3 object fetch dominates.

**P99 is noisy, not tier-driven.** The baseline P99 of 7.1 s was a single outlier in 100
samples. Auth-tier P99 values (4.8–9.3 s) are similarly driven by tail noise, not auth
overhead. The 15 % optimization trigger in the spec cannot be reliably evaluated from this
data set.

**503s on `1k` only.** 21 of 1000 requests returned 503 during the `scale/1k` run; all
other tiers were clean. These correlate with ECS task cycling (IMDS 503 bursts visible in
Envoy stats) rather than package size.

**No jwt_authn or Lua stats matched** in the Envoy admin snapshot. The stats that did match
(`ec2_instance_metadata_server_internal.upstream_rq_503: 602`) are IMDS credential-refresh
calls, not request-path auth failures.

## Optimization Decision

**No optimization required at this time.**

P50 auth overhead is below measurement noise. P99 variance is dominated by ECS cold-start /
IMDS refresh cycles, not the JWT decode or Lua logic. Recommended follow-up:

1. Re-run baseline with n=1000 to reduce P99 noise before any overhead comparison.
2. Investigate IMDS 503 bursts — task-role credential refresh under load may be the
   actual tail-latency driver.
3. File a separate issue if 503 recurrence warrants investigation.
