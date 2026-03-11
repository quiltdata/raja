# RAJA Admin: Live Tour — Redesign Spec

## Problem with the Current UI

The existing admin app ([src/raja/server/templates/admin.html](../../src/raja/server/templates/admin.html)) is a single scrolling page of flat cards. A new user lands on it and sees:

> "Issuer & JWKS. Mint RAJ. Verify RAJ. Simulate Enforcement. Control Plane. Failure Mode Test Suite."

None of these labels mean anything without prior knowledge of the system. The tools work, but there is no narrative — no way to understand *why* you would use them or how they relate.

The goal of this redesign: a first-time user should be able to open the admin app, read the home page, pick a component, and immediately understand both what it does and how to interact with it — using a live running instance.

---

## Design Principle

**Each view = one concept + one live interaction.**

The concept is explained in plain language at the top. The interaction is the proof. You learn by doing, not by reading a separate doc.

---

## Navigation Model

Replace the single scrolling page with a **persistent left sidebar nav** and a **main content area** that switches views without page reload. The URL hash reflects the active view (`#authority`, `#token`, `#enforce`, `#failures`, `#incident`, `#audit`) so views are linkable.

```
┌──────────────────────────────────────────────────────────────┐
│  RAJA Admin      Admin Key: [______________]     ● healthy   │
├──────────────┬───────────────────────────────────────────────┤
│              │                                               │
│  ◉ Overview  │   [view content here]                         │
│  ○ Authority │                                               │
│  ○ Token     │                                               │
│  ○ Enforce   │                                               │
│  ○ Failures  │                                               │
│  ○ Incident  │                                               │
│  ○ Audit     │                                               │
│              │                                               │
└──────────────┴───────────────────────────────────────────────┘
```

The header shows a live health dot (green/red) pulled from `/health` on load. It also contains a persistent **Admin Key** password input. The entered key is stored in `sessionStorage` (cleared when the tab closes). Every `fetch` call to a protected endpoint includes `Authorization: Bearer <key>`. A `401` response surfaces as an inline error banner: "Invalid or missing admin key."

The Overview and health dot load without auth (`GET /` and `GET /health` are public). All other view data requires a valid key.

---

## Views

### 1. Overview (home)

**Purpose:** Orient a new user to the whole system before they touch anything.

**Content:**

An ASCII architecture diagram showing all five roles with one-line descriptions, rendered as styled HTML:

```
 ┌──────────┐   Cedar policies    ┌─────────┐
 │   RAJA   │ ──────────────────► │   AVP   │
 │ Authority│ ◄── ALLOW/DENY ──── │ (Cedar) │
 └────┬─────┘                     └─────────┘
      │ issues RAJ / TAJ
      ▼
 ┌──────────┐   validates + routes  ┌──────────┐
 │  RAJEE   │ ─────────────────────►│    S3    │
 │ (Envoy)  │                       │ (actual) │
 └──────────┘
      ▲
      │ attaches token, routes request
 ┌──────────┐   logical path (USL)
 │  Diwan   │ ◄── developer uses boto3 normally
 └──────────┘
```

Below the diagram: a **System Status** panel showing the live output of `/health` — each dependency (jwt_secret, principal_table, mappings_table, audit_table) shown as a green/red pill.

No interactive tools on this view. Navigation only.

---

### 2. Authority (RAJA)

**Concept header:**

> RAJA is the only component that evaluates policy. It consults Cedar policies in Amazon Verified Permissions, and if it issues a token, that decision is final — no other component re-evaluates.

**Live data shown:**

- **JWKS** — the live public key set used to verify all tokens issued by this instance (`/.well-known/jwks.json`)
- **Active principals** — from `GET /principals`, shown as a table (principal → scope count)
- **Active policies** — from `GET /policies?include_statements=true`, shown as a table with full Cedar statements

**Interactions:**

- **Refresh** button to reload config, principals, and policies.
- **Create policy** — a text area for a Cedar statement, submitted to `POST /policies`. RAJA validates against the schema before sending to AVP. Returns the new `policyId`.
- **Edit policy** — click any policy row to expand its Cedar statement in an editable text area. On save, sends `PUT /policies/{id}`. The UI shows a diff of what changed.
- **Delete policy** — per-row delete button, calls `DELETE /policies/{id}`. A confirmation prompt makes clear this is permanent and that principals relying on this policy will receive DENY on next issuance.

**Why this teaches:** The user sees the live Cedar statements that RAJA is enforcing, and can modify them without a Terraform redeploy.

---

### 3. Token (RAJ / TAJ)

**Concept header:**

> A RAJ is compiled authority. RAJA evaluates policy once and encodes the result into a signed JWT. Every scope in the token represents a permission that was already decided — no policy engine runs again when the token is used.
>
> Scopes have the format `ResourceType:ResourceId:Action` — for example, `S3Object:my-bucket/reports/:s3:GetObject`.

**Interaction — Mint:**

The existing Mint RAJ form, with one addition: after minting, the token is automatically decoded and each claim is annotated inline:

```
{
  "sub": "User::alice",         // ← who this token speaks for
  "aud": "raja-s3",             // ← which service will accept it
  "scopes": [                   // ← compiled permissions (no policy eval needed)
    "S3Object:my-bucket/reports/:s3:GetObject"
  ],
  "iat": 1738800000,            // ← issued at
  "exp": 1738803600             // ← expires at (TTL enforces revocation)
}
```

**Interaction — Verify:**

Paste any token. Show decoded claims with the same inline annotations, plus a clear VALID / EXPIRED / INVALID / WRONG AUDIENCE status banner.

The minted token auto-populates into the Verify input so the flow is: Mint → see claims → verify → proceed to Enforce.

---

### 4. Enforce (RAJEE live probe)

**Concept header:**

> RAJEE does not evaluate policy. It checks one thing: is the requested scope a subset of the scopes in the token? If yes, the request is forwarded. If no, it is denied. There is no third outcome.
>
> This view proves it against a running RAJEE instance — not a simulation.

**Interaction:**

A form with three inputs: RAJEE endpoint URL (defaults to `http://localhost:10000`), a logical path (USL), and a principal. On submit, the server:

1. Mints a real short-lived TAJ (60s TTL) for the principal
2. Sends a `HEAD` request to RAJEE with the TAJ in `Authorization`
3. Returns RAJEE's HTTP status, response headers, and any `x-raja-*` diagnostic headers

The UI displays the full round-trip: token minted → request sent → response received, including all diagnostic headers so the enforcement decision is traceable.

A **RAJEE health check** runs before the probe (via `GET /probe/rajee/health?endpoint=<url>`) and surfaces a reachability error before attempting the full probe if RAJEE is unreachable.

The natural path is: Mint (Token view) → Enforce (live probe against that principal).

---

### 5. Failure Modes

**Concept header:**

> The system is fail-closed: anything ambiguous or unknown becomes a DENY. These tests prove it. Each test documents a scenario where a broken or malicious token should fail — and shows you the actual system response.

**Content:** The existing Failure Mode Test Suite, unchanged except for the header. It is already well-structured.

---

### 6. Incident Response

**Concept header:**

> Two revocation levels with different blast radii. Soft revocation removes a principal so no new tokens can be minted — existing tokens remain valid until TTL expires. Hard revocation rotates the signing key globally, invalidating every previously issued token immediately after the cutover.

#### Soft revocation — Delete principal

A principal selector (populated from `GET /principals`) with a **Delete** button. On confirm, calls `DELETE /principals/{principal}`. The UI annotates the outcome:

```text
User::alice deleted.
Existing tokens remain valid for up to 3600s.
New issuance: BLOCKED.
```

#### Hard revocation — Rotate secret

A **Rotate Secret** button with a red confirmation dialog:

> This will invalidate all currently issued tokens across the entire system. Warm Lambda containers will be flushed — brief throttle errors are expected during the transition.

On confirm, calls `POST /admin/rotate-secret` and receives a `202 Accepted` with `operation_id`. The UI immediately starts polling `GET /admin/rotate-secret/{operation_id}` (every 2s) and renders a live progress timeline:

```text
● Create new secret version      ✓
● Update Lambda env vars         ✓
● Flush warm containers          ✓  (brief throttle expected)
● Run completion probes          ⟳  in progress…
  └─ Old token: DENY             ✓
  └─ New token: ALLOW            …
● Status: SUCCEEDED
```

If the operation reaches `FAILED`, the timeline shows the failed phase and error detail inline.

#### No per-token revocation

A read-only note explains that `POST /token/revoke` returns `{"status": "unsupported"}` by design — a denylist would require every enforcement call to hit a shared mutable store, defeating the architecture. Short TTLs bound exposure instead.

**Why this teaches:** The user sees both revocation levers, understands the tradeoff (blast radius vs immediacy), and can execute either response without CLI access.

---

### 7. Audit

**Concept header:**

> Every token issuance and enforcement decision is logged. Because authority is compiled into tokens, the audit trail is complete: each entry traces back to a specific issuance event, which traces back to a specific policy evaluation.

**Content:**

The existing `/audit` endpoint, rendered as a filterable table (not raw JSON):

| Time | Principal | Action | Resource | Decision |
|---|---|---|---|---|
| 14:23:01 | User::alice | s3:GetObject | my-bucket/reports/ | ALLOW |

Filters: principal, action, resource, time range. Matches the existing query parameters on `GET /audit`.

---

## What Does Not Change

- **The failure test suite logic** — fully reused as-is in the Failures view
- **All read endpoints already present** — views consume existing GET routes without modification

---

## Files to Modify

### Frontend

| File | Change |
|---|---|
| [src/raja/server/templates/admin.html](../../src/raja/server/templates/admin.html) | Full rewrite — sidebar nav, Admin Key field, view containers |
| [src/raja/server/static/admin.js](../../src/raja/server/static/admin.js) | Full rewrite — view router, per-view data loading, annotated claim renderer, auth header on all fetches |
| [src/raja/server/static/admin.css](../../src/raja/server/static/admin.css) | Extend — sidebar layout, status pills, annotated JSON, policy diff view |

### Backend (Python)

| File | Change |
|---|---|
| [src/raja/server/dependencies.py](../../src/raja/server/dependencies.py) | Add `require_admin_auth` — reads `ADMIN_KEY` env var, timing-safe comparison, returns `401`/`500` |
| [src/raja/server/app.py](../../src/raja/server/app.py) | Add `require_admin_auth` to `GET /audit` |
| [src/raja/server/routers/control_plane.py](../../src/raja/server/routers/control_plane.py) | Add `require_admin_auth` to all token, principal, and policy endpoints; add `POST/PUT/DELETE /policies`, `GET /policies/{id}` |
| [src/raja/server/routers/probe.py](../../src/raja/server/routers/probe.py) | Add `require_admin_auth` to `POST /probe/rajee` and `GET /probe/rajee/health` |
| [src/raja/server/routers/failure_tests.py](../../src/raja/server/routers/failure_tests.py) | Replace `get_jwt_secret` pattern with `require_admin_auth` on all handlers |

### Infrastructure

**`infra/terraform/`** — Add `admin_key` variable; pass `ADMIN_KEY` env var to control plane Lambda.
