# Admin API: Authentication

## Current State

Every admin endpoint is unauthenticated. Any caller with network access to the Lambda URL can mint tokens, create or delete principals, rewrite Cedar policies, and read the full audit log.

---

## Design Decision

Use a dedicated `ADMIN_KEY` environment variable as the admin bearer token.

`Authorization: Bearer <admin-key>`

The JWT signing secret is scoped to token signing. Using it as an admin credential would over-expose it (sent on every admin request) and couple admin access revocation to token invalidation — two things that should have independent lifecycles.

An environment variable is the right level of indirection here:

- **Bootstrapping** — Terraform sets `ADMIN_KEY` at deploy time. No Secrets Manager call, no ceremony.
- **curl-friendly** — `export ADMIN_KEY=...` then `curl -H "Authorization: Bearer $ADMIN_KEY"`. Nothing else needed.
- **Independent rotation** — rotating the admin key is a Terraform variable update + redeploy. JWT signing keys and live tokens are unaffected.
- **Implementation** — timing-safe byte comparison against the env var value. No new AWS resources required.

---

## Endpoint Classification

### Public (no auth required)

| Endpoint | Reason |
| --- | --- |
| `GET /` | Static HTML — no data |
| `GET /health` | Internal/monitoring use |
| `GET /.well-known/jwks.json` | Must be public for downstream token verification |

### Protected (require `Authorization: Bearer <admin-key>`)

- `GET /audit`
- `POST /token`, `POST /token/package`, `POST /token/translation`, `POST /token/revoke`
- `GET /principals`, `POST /principals`, `DELETE /principals/{principal}`
- `GET /policies`, `POST /policies`, `GET /policies/{id}`, `PUT /policies/{id}`, `DELETE /policies/{id}`
- `POST /probe/rajee`, `GET /probe/rajee/health`
- All `/api/failure-tests/*` endpoints

---

## Server-Side Behavior

A new FastAPI dependency `require_admin_auth` is added to `dependencies.py`:

1. Reads `Authorization: Bearer <token>` from the request header. Returns `401` if the header is absent or malformed.
2. Reads `ADMIN_KEY` from the environment. Returns `500` if the variable is unset (misconfigured deployment).
3. Compares `token` to `ADMIN_KEY` using `secrets.compare_digest` (timing-safe). Returns `401` if they do not match.

All protected endpoint functions declare `_: None = Depends(dependencies.require_admin_auth)` in their signatures. The JWKS endpoint is left unguarded.

---

## Admin UI Behavior

The admin UI (`admin.html` / `admin.js`) must authenticate against the same endpoints it calls. On first load with no key stored, every API call would return `401`.

Changes to the UI:

1. A persistent **Admin Key** field (password input) appears at the top of every view.
2. The entered key is stored in `sessionStorage` (cleared when the tab closes).
3. Every `fetch` call to the admin API includes `Authorization: Bearer <key>`.
4. A `401` response surfaces a visible inline error: "Invalid or missing admin key."

The admin UI itself (`GET /`) remains publicly accessible — the HTML and JS load without auth. The key is only required when making API calls.

---

## Constraints

- Comparison must use `secrets.compare_digest` to prevent timing attacks.
- `ADMIN_KEY` must be set at deploy time (Terraform variable). An unset key is a deployment error, not a runtime one.
- `GET /health` stays public intentionally. It only checks whether dependencies are reachable, not whether they contain sensitive data.
- The JWKS endpoint stays public intentionally. External services (RAJEE, downstream verifiers) call it to fetch the public key for token verification.

---

## Implementation Checklist

### `src/raja/server/dependencies.py`

- [ ] Add `require_admin_auth` dependency function

### `src/raja/server/app.py`

- [ ] Add `require_admin_auth` to `audit_log`

### `src/raja/server/routers/control_plane.py`

- [ ] Add `require_admin_auth` to `issue_token`
- [ ] Add `require_admin_auth` to `issue_package_token`
- [ ] Add `require_admin_auth` to `issue_translation_token`
- [ ] Add `require_admin_auth` to `revoke_token`
- [ ] Add `require_admin_auth` to `list_principals`
- [ ] Add `require_admin_auth` to `create_principal`
- [ ] Add `require_admin_auth` to `delete_principal`
- [ ] Add `require_admin_auth` to `list_policies`
- [ ] Add `require_admin_auth` to `create_policy`
- [ ] Add `require_admin_auth` to `get_policy`
- [ ] Add `require_admin_auth` to `update_policy`
- [ ] Add `require_admin_auth` to `delete_policy`

### `src/raja/server/routers/probe.py`

- [ ] Add `require_admin_auth` to `probe_rajee`
- [ ] Add `require_admin_auth` to `probe_rajee_health`

### `src/raja/server/routers/failure_tests.py`

- [ ] Add `require_admin_auth` to all route handlers (replacing the partial `get_jwt_secret` pattern)

### `src/raja/server/static/admin.js`

- [ ] Wrap all `fetch` calls to include `Authorization: Bearer <key>` header
- [ ] Read key from `sessionStorage`; surface `401` as an inline error

### `src/raja/server/templates/admin.html`

- [ ] Add Admin Key password input field

### `infra/terraform/`

- [ ] Add `admin_key` variable
- [ ] Pass `ADMIN_KEY` env var to control plane Lambda

---

## Test Checklist

### `tests/unit/test_admin_auth.py` (new file)

- [ ] `test_valid_key_passes` — correct key, no exception
- [ ] `test_wrong_key_rejected` — wrong key → `HTTPException(401)`
- [ ] `test_missing_header_rejected` — no `Authorization` header → `403`
- [ ] `test_unset_admin_key_returns_500` — `ADMIN_KEY` env var absent → `500`

### `tests/unit/test_control_plane_router.py` (existing)

- [ ] Verify existing direct-call tests still pass (dependency bypassed at function level)

### Protected-endpoint smoke tests (one representative per router, via `TestClient`)

- [ ] `GET /principals` without key → `401`
- [ ] `GET /principals` with wrong key → `401`
- [ ] `GET /principals` with correct key → `200`
- [ ] `GET /audit` without key → `401`
- [ ] `POST /probe/rajee` without key → `401`
- [ ] `POST /api/failure-tests/{id}/run` without key → `401`

### Public-endpoint smoke tests

- [ ] `GET /health` without key → `200`
- [ ] `GET /.well-known/jwks.json` without key → `200`
