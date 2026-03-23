# Security Audit — RAJA

## Objective

Evaluate the security posture of the RAJA authorization system against the threats most
relevant to a JWT-based, scope-enforcing data proxy: token forgery, scope escalation, and
replay attacks. Produce a risk-rated finding set with concrete remediations tied to AWS
security controls.

## Scope

| In | Out |
|----|-----|
| IAM roles (Lambda execution, DataZone) | AWS account-level controls |
| JWT lifecycle (issuance, verification, expiry) | S3 bucket policies on data buckets |
| Envoy filter chain (mTLS, JWT filter, Lua) | DataZone internal authorization logic |
| API Gateway (resource policies, throttling, WAF) | Application-layer input validation |
| Secrets Manager usage (JWT signing key) | Network perimeter / VPC design |

## Threat Model Summary

| Threat | Vector | Mitigation Layer |
|--------|--------|-----------------|
| Token forgery | Attacker crafts valid-looking JWT | Algorithm pinning + secret strength |
| Scope escalation | Legitimate user inflates scopes in token | Server-side scope binding at issuance |
| Replay attack | Captured token reused after intended window | Short expiry + `jti` claim tracking |
| Bypass via Envoy misconfiguration | `failure_mode_allow: true` in filter chain | Envoy config review + integration test |
| Credential leakage | JWT secret in environment variable or logs | Secrets Manager + log scrubbing |
| Lateral movement | Overly-broad Lambda execution role | Least-privilege IAM review |

## Approach

### 1. IAM Least-Privilege Review

For each Lambda execution role and the DataZone service role:

```bash
# Export all role policies attached to RAJA Lambda functions
aws iam list-attached-role-policies --role-name <raja-lambda-role>
aws iam get-role-policy --role-name <raja-lambda-role> --policy-name <inline>

# Use IAM Access Analyzer to find unused permissions (requires 90-day CloudTrail window)
aws accessanalyzer create-analyzer --analyzer-name raja-analyzer --type ACCOUNT
aws accessanalyzer list-findings --analyzer-arn <arn>
```

Check each role against the principle of least privilege:
- `rale_authorizer` — needs DataZone `GetSubscriptionGrant`, Secrets Manager `GetSecretValue`
  for JWT key only; must NOT have broad `datazone:*` or `s3:*`
- `rale_router` — needs `s3:GetObject` scoped to specific bucket prefixes matching granted
  scopes; must NOT have `s3:ListBucket` on all buckets
- `control_plane` — review for any wildcard resource (`"Resource": "*"`) statements

### 2. JWT Security Review

Inspect `src/raja/token.py` and `infra/envoy/authorize_lib.lua`:

- **Algorithm pinning** — verify `PyJWT` decode call specifies `algorithms=["HS256"]` (or
  RS256 if asymmetric); the `algorithms` parameter must never be `None` or omitted, which
  would allow the `alg: none` attack.
- **Secret strength** — confirm JWT signing key in Secrets Manager is ≥ 256 bits of entropy;
  document rotation schedule (recommend: 90-day automatic rotation via Lambda rotator).
- **Expiry enforcement** — verify `exp` claim is always set at issuance and always verified
  at enforcement; check Lua filter calls `jwt_obj:verify_expiry()` or equivalent.
- **`jti` / replay prevention** — assess whether short-lived tokens (< 15 min TTL) are
  sufficient or whether a `jti` blocklist (ElastiCache or DynamoDB) is warranted for
  high-value scopes.
- **Scope binding** — confirm scopes in the token are derived exclusively from DataZone
  subscription grants at issuance time, never from client-supplied claims.

### 3. Envoy Filter Chain Security

Review `infra/envoy/envoy.yaml.tmpl`, `authorize.lua`, `authorize_lib.lua`:

- **`failure_mode_deny`** — confirm `http_filters` JWT/ext_authz filter has
  `failure_mode_deny: true`; a value of `false` allows unauthenticated pass-through on
  filter error.
- **mTLS** — verify downstream and upstream TLS contexts are configured; confirm certificate
  validation is not disabled (`verify_certificate_hash` or `trusted_ca` must be set).
- **Header stripping** — confirm Envoy strips any client-supplied `Authorization` or
  `x-raja-*` headers before forwarding to the upstream S3/Lambda target.
- **Lua sandbox** — confirm `authorize.lua` does not use `io`, `os`, or `require` for
  network calls; Lua in Envoy runs in a restricted sandbox but explicit checks are warranted.

Write an integration test that asserts a request with an expired token returns 401, not 200,
through the full Envoy filter chain.

### 4. API Gateway Security

```bash
# Check resource policy restricts invocation to known principals
aws apigateway get-rest-api --rest-api-id <id>
aws apigateway get-stage --rest-api-id <id> --stage-name prod

# Verify throttling limits are set
aws apigateway get-stage --rest-api-id <id> --stage-name prod \
  --query 'defaultRouteSettings.{throttle:throttlingBurstLimit}'
```

- **Resource policy** — confirm API Gateway resource policy allows invocation only from the
  Envoy task's VPC endpoint or security group; deny `*` principal.
- **Throttling** — verify per-route burst and rate limits are set; absence allows
  unauthenticated callers to exhaust Lambda concurrency.
- **WAF** — assess whether AWS WAF with rate-based rules is warranted; recommended for
  public-facing endpoints.
- **Access logging** — confirm CloudWatch access logs are enabled on all stages; log format
  must include `$context.authorizer.error` to surface auth failures.

### 5. Secrets Management

```bash
# Verify no Lambda env vars contain plaintext secrets
aws lambda get-function-configuration --function-name <raja-fn> \
  --query 'Environment.Variables'

# Check Secrets Manager secret has rotation configured
aws secretsmanager describe-secret --secret-id <jwt-secret-arn> \
  --query '{RotationEnabled:RotationEnabled,NextRotationDate:NextRotationDate}'
```

- Confirm `JWT_SECRET_ARN` is the only secret-related env var; the actual value must not
  appear in Lambda configuration or CloudWatch logs.
- Verify Secrets Manager resource policy restricts `GetSecretValue` to the specific Lambda
  execution role ARNs only.
- Add `secretsmanager:RotateSecret` automation; document rotation runbook.

### 6. Remediation Prioritization

Rate each finding: **CRITICAL** (fix before next deploy) / **HIGH** (fix within sprint) /
**MEDIUM** (fix within quarter) / **LOW** (document and accept).

## Deliverables

1. **Security findings report** (`docs/audits/security-audit-results.md`) with risk ratings
2. **GitHub issues** filed for CRITICAL and HIGH findings with `security` label
3. **Threat model diagram** (`docs/audits/threat-model.md`) updated with mitigations
4. **Integration test** — expired/tampered token rejected end-to-end through Envoy
5. **IAM policy diffs** — least-privilege policy documents for each Lambda execution role

## Success Criteria

| Control | Target |
|---------|--------|
| `alg: none` JWT attack | Blocked — `algorithms` param explicit in all decode calls |
| Token expiry enforcement | Verified in unit test + Envoy integration test |
| Lambda roles with wildcard resource | 0 |
| Plaintext secrets in Lambda env vars | 0 |
| Secrets Manager rotation enabled | Yes, ≤ 90-day schedule |
| `failure_mode_deny` in Envoy filter | `true` confirmed in config and tested |
| API Gateway throttling configured | Yes, on all stages |
