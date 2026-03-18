# Grant Stress Test Plan

## Goal

Systematically document the real end-to-end authorization path for package grants
and force each failure mode to happen on demand.

This plan exists because the current integration tests prove only that
`DataZoneService` works from a developer machine with developer credentials.
They do **not** prove that the deployed RALE authorizer Lambda:

- sees the same principal data,
- is configured against the same stack,
- has the same DataZone visibility,
- observes the same consistency window,
- or interprets the same package listing / grant state.

The objective is to replace "this should be the same path" with recorded evidence
for each step.

---

## Working Hypotheses

### What we may be missing

1. **Caller identity is part of the system, not just the principal header.**
   The authorizer receives `x-raja-principal`, but all DataZone reads are executed
   by the Lambda role, not by Ernest's or Kevin's local IAM user.

2. **Our current integration tests exercise the library, not the deployed trust boundary.**
   `tests/integration/test_seed_users.py` and
   `tests/integration/test_package_grant.py` call `DataZoneService` directly with
   local boto3 credentials. That is a different execution context from
   `lambda_handlers/rale_authorizer/handler.py`.

3. **We are conflating "same package path" with "same effective request".**
   Ernest and Kevin can choose the same package in the CLI while still hitting
   different endpoints, different Lambda versions, different config sources, or
   different consistency windows.

4. **Membership and grant checks may be correct independently but wrong in composition.**
   The current path is:
   `principal -> GetUserProfile -> user_id -> ListProjectMemberships -> project_id -> SearchListings -> ListSubscriptionRequests`.
   A subtle bug anywhere in that chain can produce the same final DENY.

5. **Eventual consistency may be affecting reads differently across APIs.**
   We already know `list_project_memberships` can stay stale after deletes. We do
   not yet know whether `search_listings` and `list_subscription_requests` have
   similar lag or whether Lambda sees different freshness than local callers.

---

## Path Inventory

We need to test and document these as separate paths, not one blended "RALE works"
path.

### Path A: Local library path

- Entry: direct Python / pytest
- Caller: developer AWS credentials
- Code: `src/raja/datazone/service.py`
- Existing coverage:
  [tests/integration/test_seed_users.py](/Users/ernest/GitHub/raja/tests/integration/test_seed_users.py),
  [tests/integration/test_package_grant.py](/Users/ernest/GitHub/raja/tests/integration/test_package_grant.py)
- What it proves: DataZone state is discoverable locally
- What it does **not** prove: Lambda can discover the same state

### Path B: Local CLI -> deployed authorizer

- Entry: `uv run rale`
- Caller to DataZone: Lambda execution role
- Principal source: `x-raja-principal`
- Config source:
  [src/raja/rale/config.py](/Users/ernest/GitHub/raja/src/raja/rale/config.py)
- Authorizer code:
  [lambda_handlers/rale_authorizer/handler.py](/Users/ernest/GitHub/raja/lambda_handlers/rale_authorizer/handler.py)
- What it proves: deployed authorizer can mint a TAJ for that request

### Path C: Direct HTTP -> deployed authorizer

- Entry: `curl` or small script against `RALE_AUTHORIZER_URL` / `RAJEE_ENDPOINT`
- Caller to DataZone: Lambda execution role
- Purpose: isolate CLI config issues from authorizer logic issues

### Path D: Lambda-internal introspection path

- Entry: temporary debug instrumentation in the authorizer Lambda
- Caller to DataZone: Lambda execution role
- Purpose: capture raw intermediate facts instead of only ALLOW / DENY

This is the path we currently lack, and it is the most important one.

---

## Key Difference Between RALE and the Current Integration Tests

The integration tests currently validate:

1. local boto3 credentials can resolve the user profile,
2. local boto3 credentials can list project memberships,
3. local boto3 credentials can search listings,
4. local boto3 credentials can see accepted subscription requests.

The deployed authorizer validates something else:

1. Lambda receives a path and principal header,
2. Lambda parses unpinned or pinned USL,
3. Lambda resolves package hash via `quilt3` when needed,
4. Lambda uses its own IAM role to call DataZone,
5. Lambda selects the first matching project in owner/users/guests order,
6. Lambda checks listing ownership or accepted subscription request,
7. Lambda either mints a TAJ or returns 403.

Those are not equivalent tests.

---

## Investigation Principles

1. **Record exact inputs and outputs at every hop.**
   No more "Kevin used the same package".
   We need principal, raw request path, resolved manifest hash, authorizer URL,
   router URL, DataZone domain ID, project IDs, listing ID, and subscription IDs.

2. **Test one variable at a time.**
   Same endpoint + different principal.
   Same principal + different endpoint.
   Same endpoint + same principal + local path versus Lambda path.

3. **Force known-bad states intentionally.**
   Delete membership, remove grant, point to stale endpoint, or deny a DataZone
   permission in a temporary stack variant.

4. **Capture evidence in files, not terminal memory.**
   Every run should produce a durable artifact.

---

## Required Artifacts

Create a timestamped investigation directory per run, for example:

```text
tmp/grant-stress/2026-03-17T1530Z/
```

Each run should save:

- `request.json`
  Exact principal, package, URLs, and environment source.
- `local-datazone.json`
  Local direct `DataZoneService` observations.
- `authorizer-response.json`
  Raw HTTP response from deployed authorizer.
- `lambda-debug.json`
  Temporary debug payload from authorizer, if instrumentation is enabled.
- `comparison.md`
  One-page summary of what matched and what diverged.

This can be done with ad hoc scripts first; it does not need to be elegant.

---

## Phase 1: Baseline the Real Requests

### Objective

Prove whether Ernest and Kevin are actually sending the same request to the same deployment.

### Steps

1. For Ernest and Kevin, capture:
   - `aws sts get-caller-identity`
   - effective `RALE_AUTHORIZER_URL`
   - effective `RALE_ROUTER_URL`
   - effective `RAJEE_ENDPOINT`
   - whether values came from env, `~/.config/raja/cli.toml`, or Terraform outputs
   - selected package URI and final authorizer request path

2. Add a temporary CLI debug mode or a one-off print wrapper around
   `run_authorize()` to dump:
   - principal after `_principal_id()`
   - selected USL
   - derived authorizer path
   - final authorizer URL

3. Re-run with:
   - Ernest principal against Ernest config
   - Kevin principal against Ernest config
   - Kevin principal against Kevin config

### Expected output

A table with four columns:

- actor
- effective endpoint set
- principal sent
- response body / status

### Decision point

If Ernest and Kevin are not hitting the same URLs, stop treating this as one bug.

---

## Phase 2: Compare Local DataZone View to Lambda DataZone View

### Objective

Prove whether the Lambda role sees the same DataZone facts that local developers see.

### Steps

1. Write a throwaway debug path in the authorizer Lambda that returns, for a given
   principal and package:
   - result of `_get_user_id_for_principal`
   - raw `list_project_memberships` hits for owner/users/guests projects
   - selected project ID
   - package listing match
   - owner project on the listing
   - accepted subscription request match

2. Put the debug route behind an explicit env flag such as:
   `RALE_DEBUG_AUTH=1`

3. Return structured JSON, not logs only.
   Logs are useful, but JSON makes comparison mechanical.

4. From a developer machine, run the same lookup twice:
   - once locally with direct `DataZoneService`
   - once via the debug Lambda endpoint

5. Diff the outputs for:
   - user ID mismatch
   - missing membership in Lambda only
   - missing listing in Lambda only
   - missing subscription in Lambda only

### Manual hack if needed

If exposing a debug route feels too ugly, temporarily replace the normal 403 body
with an expanded diagnostic payload and revert it after the investigation.

### Expected output

A single side-by-side JSON comparison for:

- Ernest + known-working package
- Kevin + failing package

This phase should answer the central question: is the divergence local versus Lambda,
or principal-specific inside the same Lambda path?

---

## Phase 3: Force Each Failure Mode

### Objective

Make every suspected branch fail on purpose so we know the observable signature of each.

### Failure Matrix

1. **Unknown principal**
   - Input: fake ARN
   - Expected: `principal project not found`

2. **Profile exists, membership missing**
   - Remove one user from all test projects
   - Wait and poll until local and Lambda agree they are absent
   - Expected: same deny body as above, unless we improve diagnostics

3. **Membership exists, package grant missing**
   - Remove or reject the subscription request for users/guests
   - Expected: 403 with package metadata but no project-not-found error

4. **Listing missing**
   - Use a known non-seeded package name
   - Expected: no listing match, deny or explicit not-found depending on instrumentation

5. **Lambda permission missing**
   - Temporarily remove one DataZone action from the authorizer role in a throwaway stack
   - Candidate actions:
     - `datazone:GetUserProfile`
     - `datazone:ListProjectMemberships`
     - `datazone:ListSubscriptionRequests`
     - `datazone:SearchListings`
   - Expected: current code likely collapses these into generic 503 or false negatives

6. **Endpoint drift**
   - Point CLI to an older or alternate authorizer URL intentionally
   - Expected: same principal and package, different outcome

### Important note

The current integration tests should not be treated as adequate until they can be
made to fail reliably for cases 2, 3, and 4.

---

## Phase 4: Measure Consistency Windows

### Objective

Quantify DataZone lag instead of hand-waving at "eventual consistency".

### Steps

1. For membership changes:
   - add membership
   - delete membership
   - poll every 5 seconds from both:
     - local `DataZoneService`
     - Lambda debug endpoint

2. For package grant changes:
   - create subscription request
   - accept subscription request
   - if possible, revoke or replace grant by creating a package without a subscription
   - poll `has_package_grant` locally and through Lambda

3. Record first-seen timestamps for:
   - `get_user_profile`
   - `list_project_memberships`
   - `search_listings`
   - `list_subscription_requests`

### Output

A short table:

- mutation
- API
- local visible at
- Lambda visible at
- lag delta

This tells us whether Kevin may simply have landed in a propagation gap.

---

## Phase 5: Promote the Investigation into Tests

### Objective

Turn the useful parts of the manual stress test into durable regression coverage.

### Candidate additions

1. **Config parity test**
   A test or helper that prints the effective RALE endpoints and source of truth.

2. **Authorizer black-box integration test**
   Hit the deployed authorizer endpoint directly and assert on the full response body
   for:
   - known good principal
   - fake principal
   - principal in project without grant

3. **Diagnostic comparison harness**
   A temporary script that runs:
   - local `DataZoneService` lookup
   - remote authorizer debug lookup
   and writes a diff artifact.

4. **Eventual consistency characterization test**
   Probably not for CI, but useful as an operator script under `scripts/`.

5. **Stronger negative-path unit tests**
   Especially around:
   - `_find_subscription_request`
   - `_subscription_matches`
   - `delete_project_membership`
   - response shaping in `rale_authorizer`

---

## Concrete Test Matrix

Use at least these cases.

| Case | Caller path | Principal | Package | Expected |
| ---- | ----------- | --------- | ------- | -------- |
| A1 | local service | ernest | seeded granted package | allow |
| A2 | local service | kevin | seeded granted package | allow |
| B1 | deployed authorizer | ernest | seeded granted package | allow |
| B2 | deployed authorizer | kevin | seeded granted package | allow or reproduce deny |
| B3 | deployed authorizer | fake user | seeded granted package | deny: no project |
| C1 | deployed authorizer | valid member | ungranted package | deny: no grant |
| C2 | deployed authorizer | valid member | nonexistent package | deny: no listing |
| D1 | deployed authorizer with restricted IAM | ernest | seeded granted package | fail with explicit service-unavailable or diagnostic |

The missing row today is B2 with enough diagnostics to explain why it differs from A2.

---

## Minimal Instrumentation Changes

These are acceptable even if they are ugly and temporary.

1. Add a debug mode in
   [lambda_handlers/rale_authorizer/handler.py](/Users/ernest/GitHub/raja/lambda_handlers/rale_authorizer/handler.py)
   that emits intermediate authorization facts as JSON.

2. Add a one-off script under `scripts/` that:
   - loads principal + package
   - runs local `DataZoneService`
   - calls the remote debug endpoint
   - writes both outputs to `tmp/grant-stress/...`

3. Add a CLI flag or env var to print effective resolved config from
   [src/raja/rale/config.py](/Users/ernest/GitHub/raja/src/raja/rale/config.py).

4. Stop swallowing `ValidationException` in membership deletion once the
   investigation is complete, because it hides whether the setup mutation happened.

---

## Order of Execution

1. Baseline Ernest and Kevin endpoint/config parity.
2. Add Lambda debug payload.
3. Run Ernest local versus Lambda comparison.
4. Run Kevin local versus Lambda comparison.
5. Force grant-missing and membership-missing states manually.
6. Measure propagation lag.
7. Convert stable findings into tests or scripts.

Do not skip step 1. If the two developers are not hitting the same stack, the rest
of the reasoning collapses.

---

## Success Criteria

We are done when all of the following are true:

1. We can explain Kevin's failure with a recorded intermediate fact, not a theory.
2. We can force at least three distinct deny modes on demand.
3. We have one artifact that compares local and Lambda DataZone observations for
   the same principal and package.
4. We know whether the bug is:
   - config drift,
   - Lambda IAM / runtime behavior,
   - DataZone consistency,
   - principal resolution,
   - listing lookup,
   - or subscription matching.
5. At least one new automated black-box test exists for the deployed authorizer path.

---

## Likely Outcomes

The most probable outcomes are:

1. **Config drift**
   Kevin is not calling the same deployed authorizer URL as Ernest.

2. **Lambda/local visibility mismatch**
   Local developers can see DataZone state that the Lambda role cannot or does not.

3. **Grant lookup mismatch**
   The project membership resolves, but `_find_subscription_request()` is not matching
   the actual accepted subscription shape returned by DataZone.

4. **Consistency window**
   Kevin's user or grant state exists in one API surface but not yet in the one the
   authorizer depends on.

This plan is designed to distinguish those outcomes quickly and with minimal faith.
