# Why Auth-Enabled RAJEE Tests Are Failing

## Context

[11-enable-auth.md](11-enable-auth.md) assumed the request path would be:

1. Envoy `jwt_authn` validates the JWT and forwards the payload in `x-raja-jwt-payload`.
2. The Lua filter reads `grants` and returns ALLOW or DENY (403).

The integration run shows a different behavior.

## Failure Signature

From the `./poe all` output:

- All RAJEE Envoy S3 operations return **401 Unauthorized** (including allowed prefix writes).
- The negative test expecting **403** receives **401** instead.

That means the request is being rejected **before** the Lua authorization filter makes a grants decision. The Lua path only returns 403 when it sees a valid JWT payload and then rejects the grants. Instead, we are in the "JWT missing/invalid" branch.

## What This Implies

The `jwt_authn` filter is not producing `x-raja-jwt-payload` for these requests. That only happens when the JWT is missing or fails validation (issuer, audience, signature, or JWKS fetch).

So the failure is not in the grants logic or the prefix rules. It is in JWT validation or in how the JWT reaches Envoy.

## Likely Causes (Ordered by Probability)

### 1. Tokens are signed with the wrong secret

The tests mint tokens locally using `get_jwt_secret()`. If Secrets Manager access fails, the helper silently falls back to `"test-secret-key-for-local-testing"`.

That fallback token will **never** validate against the JWKS from the control plane, so Envoy returns 401 for everything.

Evidence to look for:

- No `JWTSecretArn` output available, or `get_secret_value` failing locally.
- Envoy logs showing `JWT verification failed` or `JWKS` mismatches.

### 2. Envoy cannot fetch JWKS from the API Gateway

If the Envoy tasks cannot reach `/.well-known/jwks.json`, `jwt_authn` never validates tokens.

Possible reasons:

- VPC egress issues (NAT misconfigured or no route to internet).
- DNS resolution failures for the API Gateway host.
- TLS handshake failure to the JWKS endpoint.

This also produces 401 for all requests.

### 3. Issuer or audience mismatch

The tokens include:

- `iss = https://7tp2ch1qoj.execute-api.us-east-1.amazonaws.com/prod`
- `aud = ["raja-s3-proxy"]`

Envoy expects the same values in the `jwt_authn` config. Any mismatch (trailing slash differences, different base URL, or wrong audience) invalidates the token.

### 4. `kid` header mismatch (less likely)

JWKS exposes `kid = raja-jwt-key`, but the tokens are issued without a `kid` header. Envoy *should* accept a single key JWKS without `kid`, but if it doesn’t, tokens will be rejected.

## Why This Blocks the Intended 403 Behavior

The Lua filter only runs after `jwt_authn` succeeds. Since JWT validation fails, we never reach the grants logic and never generate a 403.

This explains why both the allowed and disallowed prefix tests return 401.

## Next Diagnostics (Minimal Steps)

1. Confirm the token is signed with the same secret as JWKS.
   - Verify `get_jwt_secret()` does not fall back to the test secret.
   - If in doubt, mint the token via `/token` with `token_type=rajee` instead of local signing.
2. Check Envoy logs for `jwt_authn` rejection reasons.
3. Verify JWKS endpoint reachability from the Envoy task (DNS + HTTPS).
4. Confirm issuer and audience match exactly.

## Fix Options

1. **Stop silent fallback on JWT secret fetch** in test helpers.
   - Fail fast if Secrets Manager is unreachable.
2. **Use the control-plane token endpoint** for integration tests so tokens always match JWKS.
3. **Pin issuer/audience explicitly** in the test helper to match Envoy config.
4. **Add `kid` header** in test-issued tokens (optional but safer).

## Summary

The integration failures are not caused by the grants or prefix logic in Lua. They are caused by JWT validation failing before Lua runs, which is why everything returns 401. The most likely root cause is that tests are signing with the wrong secret due to a silent fallback, or Envoy cannot retrieve the JWKS. Fixing JWT validation will restore the intended 403 behavior for unauthorized prefixes.
