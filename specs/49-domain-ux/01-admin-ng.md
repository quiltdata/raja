# Admin UI Next Generation

## Problem with the current design

`admin.html` was designed around a single concern: RAJ token operations. Its eight sidebar tabs
(Overview, Access Policy, The Token, Enforcement, Failure Modes, Revocation, Audit, About) are
tool-centric and flat. There is no surface for the DataZone domain structure that is now the
actual source of truth, no way to see or manage test data, and no support for the RALE end-to-end
flow. A user trying to understand the system has to mentally reconstruct the pipeline from
disconnected tool panels.

---

## Organizing principle: three columns, three modes

The redesign organizes the UI around how a user actually works with the system, not around which
HTTP endpoint they are calling.

```text
┌─────────────────────┬──────────────────────┬───────────────────────┐
│  DOMAIN STRUCTURE   │    TEST DATA         │   LIVE EXECUTION      │
│  (static, read)     │    (managed objects) │   (dynamic flows)     │
└─────────────────────┴──────────────────────┴───────────────────────┘
```

Each column has a distinct job:

- **Domain Structure** — what is deployed and wired together; read-only census of the system
- **Test Data** — principals, packages, and grants; the objects that exercises the authorization graph
- **Live Execution** — run the RALE flow, prove enforcement, run failure tests

This maps naturally onto how someone reasons about the system: first understand what exists, then
understand who has access to what, then see it work (or fail correctly).

---

## Column 1: Domain Structure

A structural view of everything that was provisioned. Not a configuration editor — a live census.

### DataZone panel

Shows the DataZone domain as the root of the authorization graph:

- Domain name, ID, region, and portal link (one-click deep link to the DataZone console)
- Owner project (the seeder project that creates listings)
- Registered asset type: `QuiltPackage` with current revision

Each of these is a row with a live status badge — green if reachable, amber if unverified, red if
the API call fails. A single "Refresh" button re-fetches all of them at once.

### RAJ Stack panel

The RAJA control plane components as a checklist of endpoints:

- RAJA server URL + `/health` result
- RALE Authorizer URL + reachability
- RALE Router URL + reachability
- RAJEE endpoint + reachability
- JWKS endpoint + current key IDs

No tabs. No nested navigation. One scroll, every component.

---

## Column 2: Test Data

Three linked tables that together represent the full authorization graph. Changes in one ripple
visibly to the others.

### Principals table

Columns: Principal ID | DataZone project name | project ID | scope count | last token issued

Each row is expandable to show the scopes stored in DynamoDB. A "Delete" action in each row maps
directly to the soft-revocation concept. An "Add principal" form at the top calls `POST /principals`.

### Package listings table

Columns: Package name (Quilt URI) | listing ID | owner project | asset type | subscriptions

Derived from DataZone listings. Read-only (listings are created by the seed scripts, not the UI).
Each row links out to the DataZone listing in the console.

### Grants table

The cross-product view: which principal (project) has an ACCEPTED subscription to which listing.
This is the actual authorization state — the thing RAJ reads at mint time.

Columns: Principal | Package | Grant status | Subscription ID

Rows with `ACCEPTED` are green. `PENDING` or absent rows are amber or empty. This makes the
authorization graph concrete and scannable.

---

## Column 3: Live Execution

Two interactive flows and one test suite, presented as sequential steps rather than isolated
panels.

### The RALE Flow

A step-by-step walkthrough of the RALE CLI flow, in the browser:

#### Step 1 — Select

- Dropdown of packages pulled live from the actual Quilt registry configured in the deployment
- On selection, lists the real files in that package with their actual sizes
- File picker produces the USL, displayed in a read-only field
- Maps to `run_select` in the CLI

#### Step 2 — Authorize

- Principal field pre-filled from the global selector (a real IAM ARN from `GET /principals`)
- "Request TAJ" calls the RALE authorizer with `x-raja-principal` + the USL
- On success: decoded TAJ claims displayed inline — `sub`, `grants`, `manifest_hash`,
  `package_name`, `registry`, `iat`, `exp` — each annotated with a one-line explanation of
  what the claim means and why it is there
- On 403: shows the denial reason and which DataZone grant is missing
- Maps to `run_authorize` in the CLI

#### Step 3 — Fetch

- "Fetch via RALE Router" sends the TAJ in `x-rale-taj` to the router
- Shows response status, byte count, and a content preview
- Shows the diagnostic headers the router emitted
- Maps to `run_fetch` in the CLI

Each step is unlocked only after the previous step completes — this mirrors the CLI's sequential
phases and makes the dependency explicit. A "Reset" button at any point starts over.

### Failure Tests

Not buried in a separate tab. Presented as a test runner directly below the RALE flow:

- Category selector (expired token, wrong audience, tampered claims, missing scope, etc.)
- Each test shows its hypothesis in plain English before running
- "Run all" button runs the full suite against the live RAJEE endpoint
- Results: pass (denial received as expected) or fail (unexpected allow or wrong error)
- Export as JSON for CI evidence

The hypothesis is shown first because the interesting thing is not "what happened" but "what was
supposed to happen, and did it?"

### Revocation

A small panel at the bottom of the execution column, not a separate section:

- Soft revocation: principal dropdown → Delete (same as the principals table, mirrored here for
  the incident flow)
- Hard revocation: Rotate Secret button with a confirmation step and a timeline showing when the
  rotation completed
- Static note: per-token revocation is unsupported by design, with one sentence explaining why

---

## Header: always visible

The persistent header shrinks to just three things:

1. **Admin key field** — with the lock/unlock icon (same as today)
2. **System health summary** — single color dot + text: "All systems live" / "1 component
   unreachable" / "Auth error" — clicking it scrolls to the Domain Structure column
3. **Current principal** — a dropdown of known principals; selecting one pre-fills it across all
   three columns (the RALE flow, the grants table filter, the soft-revocation dropdown)

The active principal is the binding thread across all three columns. The dropdown is populated
exclusively from `GET /principals` — the live DynamoDB table that holds the real IAM ARNs seeded
from `RAJA_USERS`. Selecting one filters column 2 to show only that principal's DataZone project
and grants, and pre-fills column 3's RALE flow principal field. No example values, no placeholders:
if a principal is not in the live control-plane response it does not appear here.

---

## Navigation model

No sidebar. No tab switching. The page is a single scrollable canvas with three sticky column
headers. On narrow viewports the three columns stack vertically in order: Structure → Data →
Execution.

Within each column, sections are collapsed by default on first load (except health indicators,
which are always visible). Expand controls are "+/-" toggles per section, not tabs. Deep-linking
works via URL fragments that identify a column + section, not just a top-level view.

An "About" footer panel (collapsed by default) retains the narrative reading list from the current
design.

---

## What this buys

| Current | Next |
| --- | --- |
| Eight disconnected tabs | Three columns reflecting the actual mental model |
| No DataZone surface | Domain, projects, listings, grants all first-class |
| No test data management | Principals/packages/grants as managed tables |
| RALE flow spread across Enforcement + manual CLI | Integrated step-by-step RALE runner |
| Failure tests isolated from execution | Failure tests adjacent to the flow they test |
| Principal must be typed repeatedly | Global principal selector syncs all panels |
| Token section divorced from its effect | Authorize step shows claims annotated in context |

The current UI asks the user to understand the system before they can use the UI. The new UI
**teaches the system by using it** — the RALE flow is the curriculum, the domain structure is the
reference, and the test data is the evidence.
