# Incident Response: Token Invalidation via Secret Rotation

RAJA has two levels of revocation with different blast radii and immediacy.

---

## Soft Revocation — Principal Deletion

```
DELETE /principals/{principal}
```

Stops new token issuance for the named principal. Existing tokens remain cryptographically valid and will continue to work until they expire on their own TTL.

**Use when:** A principal should lose access going forward but no token compromise is suspected. The TTL bounds the residual window (default 3600s, TAJ 60s).

---

## Hard Revocation — Secret Rotation

Rotate the JWT signing secret in AWS Secrets Manager, then force a Lambda cold start to flush the cached secret value.

**Procedure:**

1. Create a new secret value in Secrets Manager (update the existing secret version, or create a new secret and update `JWT_SECRET_ARN`)
2. Update the `JWT_SECRET_ARN` environment variable on both the **Control Plane Lambda** and the **RALE Authorizer Lambda** — this forces a cold start and flushes the in-memory cache
3. All existing tokens (signed with the old secret) now fail signature verification → enforcer returns DENY
4. New tokens are issued and verified using the new secret

**Use when:** A token has been compromised, a principal may have obtained tokens before deletion, or a system-wide reset is required.

### POST /admin/rotate-secret

Performs the full hard revocation in a single atomic call:

1. Generates a new secret value
2. Writes it to Secrets Manager (same ARN, new version)
3. Updates a `SECRET_VERSION` env var (current timestamp) on both the **Control Plane Lambda** and the **RALE Authorizer Lambda**, forcing a cold start and flushing the cached secret

**Authentication:** Requires out-of-band credentials (not JWT). If existing tokens are compromised, a JWT-authenticated revocation endpoint provides no protection.

**Response:** `{"rotated": true, "version": "<new-secret-version-id>"}`

---

## Why No Per-Token Denylist

`POST /token/revoke` returns `{"status": "unsupported"}` and will remain that way. A denylist would require every enforcement call to read from a shared mutable store, reintroducing exactly the per-request overhead the architecture is designed to eliminate.

Secret rotation is the zero-denylist path to immediate, universal invalidation.

---

## Summary

| Scenario | Mechanism | Effect |
| --- | --- | --- |
| Remove future access for a principal | `DELETE /principals/{principal}` | Stops new issuance; existing tokens live to TTL |
| Invalidate all existing tokens immediately | `POST /admin/rotate-secret` | Existing tokens fail signature check |
| Per-token revocation | Not supported by design | Use short TTLs to bound exposure |
