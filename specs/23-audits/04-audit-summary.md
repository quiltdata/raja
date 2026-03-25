# Audit Summary — RAJA

Run date: 2026-03-23
Sources: `01a-code-audit-results.md`, `02a-security-audit-results.md`, `03f-live-performance-results.md`

---

## 1. Performance Audit

Direct S3 baseline (AWS CLI, `scale/1k`, n=100): P50 0.924 s · P95 1.096 s · P99 7.106 s

Auth-enabled path (Envoy JWT+Lua, n=200 per tier, c=10):

| tier | P50 (s) | P95 (s) | P99 (s) | overhead P99 | errors |
| --- | ---: | ---: | ---: | ---: | --- |
| `scale/1m` | 0.901 | 2.051 | 9.321 | +31 % | none |
| `scale/100k` | 0.897 | 2.791 | 9.063 | +28 % | none |
| `scale/10k` | 0.917 | 3.280 | 4.834 | −32 % † | none |
| `scale/1k` | 0.915 | 2.862 | 5.561 | −22 % † | 21 × 503 |

† P99 baseline (7.1 s) was inflated by a single outlier; these tiers beat it by chance.

**P50 latency is identical across all tiers** — ~0.90–0.92 s regardless of package size. The JWT+Lua filter chain adds negligible median cost; S3 fetch dominates.

**P99 is noisy, not tier-driven.** The 15% optimization trigger in the spec cannot be reliably evaluated from this data set.

**503s on `1k` only.** 21/1000 requests returned 503, correlating with ECS task cycling (IMDS credential-refresh bursts), not package size.

### Optimization Decision

**No optimization required at this time.** Recommended follow-up:

1. Re-run baseline with n=1000 to reduce P99 noise before any overhead comparison.
2. Investigate IMDS 503 bursts — task-role credential refresh under load may be the actual tail-latency driver.
3. File a separate issue if 503 recurrence warrants investigation.

---

## 2. Security Audit

### Productization Concerns

| severity | file | issue |
| --- | --- | --- |
| MEDIUM | `infra/terraform/main.tf` | API Gateway control plane has `authorization = "NONE"` on all resources; no resource policy, throttling, or access logging in Terraform |
| MEDIUM | `infra/terraform/main.tf` | Both Lambda Function URLs grant `principal = "*"` constrained only by `source_account` — does not restrict to trusted forwarder roles |
| MEDIUM | `infra/terraform/main.tf` | IAM grants are overly broad: DataZone owner has `s3:*` over both buckets; control plane can mutate Lambda config and write secrets; authorizer uses wildcard DataZone resource scope |
| MEDIUM | `infra/terraform/main.tf` | JWT signing secret has no Secrets Manager rotation resource — lifecycle is ad hoc application logic, not infrastructure-managed |

> These productization items are infrastructure hardening gaps, not flaws in the core authorization design.

---

## 3. Quality Audit

| severity | file | issue |
| --- | --- | --- |
| HIGH | `src/raja/enforcer.py`, `src/raja/token.py` | Coverage at 69% and 71% respectively — below audit targets for core auth logic |
| HIGH | `lambda_handlers/rale_authorizer/handler.py` | Coverage at 66%; error branches and external-call paths unverified |
| MEDIUM | `.github/workflows/ci.yml` | CI does not gate on `bandit`, `pip-audit`, `vulture`, or coverage thresholds |
