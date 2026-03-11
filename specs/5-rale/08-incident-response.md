# Incident Response: Token Invalidation

RAJA has two revocation levels with different blast radii and immediacy.

---

## Soft Revocation — Principal Deletion

```
DELETE /principals/{principal}
```

Stops new token issuance for the named principal. Existing tokens remain valid until their TTL expires (default 3600s; TAJ 60s).

**Use when:** A principal should lose future access but no token compromise is suspected.

---

## Hard Revocation — Secret Rotation

Rotates the signing key epoch globally, invalidating all previously issued tokens.

### POST /admin/rotate-secret

Executes the following steps as an async operation:

1. Create a new secret version in `JWT_SECRET_ARN`.
2. Update `JWT_SECRET_VERSION` on the Control Plane, RALE Authorizer, and RALE Router Lambdas.
3. Flush warm containers (set reserved concurrency to 0, then restore). Updating env vars alone is insufficient — warm containers retain the old config until recycled. Brief throttle errors during the flush are expected.
4. Run completion probes: old token must be rejected (`DENY`); new token (minted internally) must be accepted (`ALLOW`).
5. Mark `SUCCEEDED` after probes pass; mark `FAILED` with phase/error detail otherwise.

**Auth:** Admin key (`Authorization: Bearer <ADMIN_KEY>`). The endpoint must not rely on JWT auth, since the signing key may be the subject of the incident.

**Response:**

- `202 Accepted` with `operation_id`
- `GET /admin/rotate-secret/{operation_id}` → `PENDING | SUCCEEDED | FAILED`

**Use when:** A token has been compromised, a principal may have obtained tokens before deletion, or a full system reset is required.

---

## No Per-Token Denylist

`POST /token/revoke` returns `{"status": "unsupported"}` by design. A denylist would require every enforcement call to hit a shared mutable store — exactly the per-request overhead this architecture is built to avoid. Secret rotation provides global invalidation without it.

---

## Summary

| Scenario | Mechanism | Effect |
| --- | --- | --- |
| Remove future access for a principal | `DELETE /principals/{principal}` | Stops new issuance; existing tokens live to TTL |
| Invalidate all existing tokens globally | `POST /admin/rotate-secret` | Old-signature tokens rejected after cutover |
| Per-token revocation | Not supported | Use short TTLs to bound exposure |
