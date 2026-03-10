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

Replace the single scrolling page with a **persistent left sidebar nav** and a **main content area** that switches views without page reload. The URL hash reflects the active view (`#authority`, `#token`, `#enforce`, `#failures`, `#audit`) so views are linkable.

```
┌─────────────────────────────────────────────────────┐
│  RAJA Admin                             ● healthy   │
├──────────────┬──────────────────────────────────────┤
│              │                                      │
│  ◉ Overview  │   [view content here]                │
│  ○ Authority │                                      │
│  ○ Token     │                                      │
│  ○ Enforce   │                                      │
│  ○ Failures  │                                      │
│  ○ Audit     │                                      │
│              │                                      │
└──────────────┴──────────────────────────────────────┘
```

The header shows a live health dot (green/red) pulled from `/health` on load.

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
- **Active principals** — from `principals` endpoint, shown as a table (principal → scope count)
- **Active policies** — from `policies` endpoint, shown as a table

**Interaction:** "Refresh" button to reload config and principals.

**Why this teaches:** The user sees that RAJA has a specific issuer URL and key — making the trust model concrete.

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

### 4. Enforce (RAJEE simulation)

**Concept header:**

> RAJEE does not evaluate policy. It checks one thing: is the requested scope a subset of the scopes in the token? If yes, the request is forwarded. If no, it is denied. There is no third outcome.

**Interaction:**

The existing Simulate Enforcement form. After a decision, show the subset check visually:

```
Requested:  S3Object:my-bucket/reports/2024.csv:s3:GetObject
              ⊆ ?
Granted:    S3Object:my-bucket/reports/:s3:GetObject    ✓ (prefix match)

Decision:   ALLOW
```

For a DENY, show which scope was checked against which granted scopes and why none matched.

The token auto-populates from the Mint view so the natural path is Mint → Verify → Enforce.

---

### 5. Failure Modes

**Concept header:**

> The system is fail-closed: anything ambiguous or unknown becomes a DENY. These tests prove it. Each test documents a scenario where a broken or malicious token should fail — and shows you the actual system response.

**Content:** The existing Failure Mode Test Suite, unchanged except for the header. It is already well-structured.

---

### 6. Audit

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

- **All backend routes** — no changes to `app.py`, routers, or any Python code
- **All existing API endpoints** — the views consume the same endpoints already present
- **The failure test suite logic** — fully reused as-is

---

## Files to Modify

| File | Change |
|---|---|
| [src/raja/server/templates/admin.html](../../src/raja/server/templates/admin.html) | Full rewrite — sidebar nav, view containers |
| [src/raja/server/static/admin.js](../../src/raja/server/static/admin.js) | Full rewrite — view router, per-view data loading, annotated claim renderer |
| [src/raja/server/static/admin.css](../../src/raja/server/static/admin.css) | Extend — sidebar layout, status pills, annotated JSON, subset-check visualization |

No Python changes required.
