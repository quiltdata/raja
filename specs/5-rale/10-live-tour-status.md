# Admin UI: What's Actually Working

`06-live-tour.md` described where we wanted to go. This document is the ground truth of where we are.

---

## Status by View

| View | Nav | HTML | JS | Backend | Auth | Verdict |
|------|-----|------|----|---------|------|---------|
| Overview | ✓ | ✓ | ✓ | `GET /health` | public | **WORKS** |
| Authority | ✓ | ✓ | ✓ | JWKS, principals, policies (CRUD) | ✓ | **WORKS** |
| Token | ✓ | ✓ | ✓ | `POST /token` | ✓ | **WORKS** |
| Enforce | ✓ | ✓ | ✓ | `POST /probe/rajee` | ✓ | **WORKS** |
| Failures | ✓ | ✓ | ✓ | **MISSING** | ✓ | **BROKEN** |
| Incident | ✓ | ✓ | ✓ | delete principal, rotate secret | ✓ | **WORKS** |
| Audit | ✓ | ✓ | ✓ | `GET /audit` | ✓ | **WORKS** |

Six of seven views are wired end-to-end. One is broken.

---

## The Only Gap: Failures View Backend

The JS expects three endpoints that don't exist in any router:

```
GET  /api/failure-tests/
POST /api/failure-tests/{testId}/run
POST /api/failure-tests/categories/{categoryId}/run
```

The old failure test logic lives in `src/raja/server/routers/failure_tests.py` but is wired to different route paths. The new JS calls the `/api/failure-tests/` prefix and the old router does not match.

**Fix:** Either rewrite `failure_tests.py` to match the new route contract, or update the JS to call the old routes. Pick one.

---

## How to Verify Each View Yourself

You need:
- The deployed URL (from `./poe show-outputs` or CloudFormation console)
- The `ADMIN_KEY` value (from AWS Secrets Manager or your `.env`)

### 1. Overview
Open the URL. You should see the architecture diagram and green health pills without entering a key.

### 2. Authority
Enter your admin key. Click **Authority**. The page should load:
- JWKS (a JSON object with a `keys` array)
- Principals table (empty is fine if none seeded)
- Policies table (empty is fine)

If you see "Invalid or missing admin key" — your key is wrong.
If you see a network error — the backend route is unreachable.

### 3. Token
Click **Token**. Enter a principal (e.g. `User::alice`) and a resource (e.g. `S3Object:my-bucket/file.txt`). Click **Mint**. You should get a decoded JWT with annotated claims.

Paste the minted token into **Verify**. You should see `VALID`.

### 4. Enforce
Click **Enforce**. The RAJEE endpoint defaults to `http://localhost:10000`. Change it to your deployed RAJEE URL if you have one, or leave it to confirm you get a reachability error (expected if RAJEE isn't running locally).

### 5. Failures
Click **Failures**. You will see a spinner or empty state — **this is the broken view**. See fix below.

### 6. Incident
Click **Incident**. The principal list should populate from `/principals`. The **Rotate Secret** button should be present but not clicked unless you intend to invalidate all tokens.

### 7. Audit
Click **Audit** and hit **Search** with no filters. You should see a table of recent issuance and enforcement events.

---

## Fix Plan

### Step 1: Fix the Failures router (1–2 hours)

File: `src/raja/server/routers/failure_tests.py`

The router needs to expose:

```python
GET  /api/failure-tests/
# Returns: { "tests": [...], "categories": [...] }

POST /api/failure-tests/{test_id}/run
# Returns: { "id": ..., "status": "pass"|"fail", "detail": ... }

POST /api/failure-tests/categories/{category_id}/run
# Returns: { "results": [...] }
```

Each test in the existing suite has an id and a category. Map them and expose through these three routes. Add `require_admin_auth` to all three.

### Step 2: Smoke-test all views end-to-end

Run through the verification steps above after the fix. Write down what breaks. That becomes the next issue.

### Step 3: Add an integration test for the admin UI routes

`tests/integration/` should have a test that hits each view's primary endpoint with a valid admin key and asserts a 200. This catches route registration regressions. See `tests/integration/test_health_auth.py` for the pattern already in use.

---

## What Is Not a Problem

- The sidebar nav, routing by hash, and view switching all work.
- Admin key persistence in `sessionStorage` works.
- Auth error banner on 401 works.
- The health dot in the header works.
- All six working views load and interact with live data.

The UI is not a failure. One backend route group is missing.
