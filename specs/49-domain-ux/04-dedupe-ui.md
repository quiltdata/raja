# Admin UI Deduplication: Zero-Overlap Layout

## Problem

The current three-column layout (`01-admin-ng.md`) shipped with several conceptual overlaps that cause
confusion:

1. **Principals = DataZone project members, shown twice.** Column 2 ("Test Data") has a flat
   Principals table. Column 1 ("Domain Structure") shows the DataZone domain and its owner project.
   But the Principals table *is* DataZone project membership — each row carries a
   `datazone_project_name` and `datazone_project_id` that duplicate the project identity already
   implicit in the Domain Structure column. The same reality is described from two angles with no
   indication they are the same thing.

2. **Delete principal exists in two places.** The inline Delete button on each principal row and the
   "Soft revocation → Delete principal" dropdown in the Execution column both call `DELETE /principals/{id}`.
   Neither is labelled as the authority; both silently agree.

3. **Stack health is a live probe, not static config.** RAJA Stack health sits in Column 1
   ("Static, read-only") but the five component checks are HTTP probes against live endpoints — the
   same class of operation as the RALE Flow and Failure Tests in the Execution column.

4. **`get_admin_structure` omits two of three projects.** Only `owner_project` is returned. The
   Users and Guests project IDs and names are not in the structure response, so they cannot be
   linked or displayed alongside the domain they belong to.

5. **No console links for projects.** The domain has a console link; individual project pages do
   not, even though project IDs are displayed.

6. **The header selector and project cards need different views of principal data.** A principal
   who is a member of two projects appears twice in the flat `/principals` response with different
   `datazone_project_id` values. That is correct for authorization semantics, but noisy if the
   header selector renders those rows directly.

7. **Not everything linkable is linked.** `owner_project_name` in the Package Listings table is
   plain text; it has a DataZone console URL. The subscriptions count is a plain number; it links
   to the same listing page that already shows subscription state.

8. **Column 2 execution flow is inverted.** Stack Health appeared above RALE Flow, implying "check
   health first". The natural workflow is: select → test → verify failures → then check health as a
   diagnostic outcome.

---

## Organizing principle: membership lives inside its project

The Principals table should not exist as a standalone entity. DataZone project membership *is* the
authorization assignment. Showing principals as a flat list, then also showing projects as a flat
list, produces two incomplete views that together describe one complete thing.

The redesign folds principals into their projects so each project is a self-contained unit: identity,
scope, members, and add/remove controls in one place. This also makes the Delete button
unambiguous — it removes a member from a project, and that action lives in one place only.

---

## New column layout

Two columns. The old Column 2 (Data Graph / Authorization graph) is removed entirely.

```text
Column 1: Domain & Projects         Column 2: Stack & Execution
─────────────────────────────────   ──────────────────────────────
Kicker: "SageMaker domain census"   Kicker: "Live system"

[Domain]  (status rows)             [Package Listings]  ← 1. select target
  - Domain name (console link)        - Package (listing link)
  - Asset type + revision             - Owner project (project console link)
                                       - Subscriptions (count, links to listing)
[Projects]
  ▸ Owner  (project console link)   [RALE Flow]          ← 2. run test
      Scope: *:*:*                    unchanged; package selector draws from
      Members: [list + Delete]        Package Listings above
      [Add member form]
  ▸ Users  (project console link)   [Failure Tests]      ← 3. stress test
      Scope: S3Object:*:*             unchanged
      Members: [list + Delete]
      [Add member form]             [Stack Health]        ← 4. diagnostic
  ▸ Guests (project console link)    - RAJA server
      Scope: S3Object:*:s3:GetObject  - RALE Authorizer
      Members: [list + Delete]        - RALE Router
      [Add member form]               - RAJEE
                                       - JWKS

                                    [Secret Rotation]    ← 5. remediate
                                       rotate button + timeline
                                       (soft-delete removed)
```

The execution column now reads as a workflow top-to-bottom: pick a package, run the happy path,
run failure cases, see if anything broke, rotate if needed.

---

## Changes by file

### `control_plane.py`

**Add `_console_project_url` helper** alongside the existing `_console_domain_url` and
`_console_listing_url`:

```python
def _console_project_url(*, region: str, domain_id: str, project_id: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/datazone/home"
        f"?region={region}#/domains/{domain_id}/projects/{project_id}"
    )
```

**`get_admin_structure`**: Add `users_project` and `guests_project` to the `datazone` block, each
with the same shape as `owner_project` (`id`, `name`, `status`, `portal_url`). Add `portal_url`
to `owner_project` as well.

**`get_access_graph` / package listings**: Add `owner_project_url` to each listing row, built
with `_console_project_url` using the existing `owner_project_id`. This parallels how `listing_url`
is already constructed with `_console_listing_url`.

**`list_principals` semantics**: Do **not** deduplicate the `/principals` response. Multiple rows
for the same principal represent distinct project memberships and must remain visible so
`/admin/access-graph?principal=...` still reflects the union of that principal's memberships.

If a deduplicated view is useful for the header selector, add it as an additive summary field
instead of collapsing the primary rows. Example:

```python
return {
    "principals": principals,
    "principal_summary": [
        {
            "principal": user_id,
            "project_ids": [...],
            "project_names": [...],
        }
        for user_id in ...
    ],
}
```

This preserves backward compatibility while giving the UI a clean one-row-per-principal source.

### `admin.html`

**Column 1** — rename kicker to "SageMaker domain census", heading to "Domain & Projects":

- Remove `<details id="section-stack">` (moves to column 2)
- Rename `<details id="section-datazone">` summary to "Domain"
- Remove `<details id="section-principals">` and its add-principal form
- Add `<details id="section-projects">` containing three sub-cards, one per project:
  - Header: project name + project ID + DataZone console link
  - Scope definition (read-only text)
  - Member table `<tbody id="tier-owner-body">` / `tier-users-body` / `tier-guests-body`
  - Per-project add-member `<form data-tier="owner|users|guests">`

**Column 2** — replaces old Column 3; removes old Column 2 (Data Graph) entirely. Reorder sections
top to bottom: `section-listings` → `section-rale` → `section-failures` → `section-stack` →
`section-rotation`. Delete `section-access` and its container. Move `section-listings` from the
old Column 2 to the top of this column. Add `<details id="section-stack">` below Failure Tests
(moved from column 1). Rename `<details id="section-revocation">` to `id="section-rotation"` with
summary "Secret Rotation". Remove the soft-revocation step-card entirely (principal dropdown,
Refresh button, Delete button, `revoke-soft-output`). Keep only the hard-revocation step-card.

**Package Listings table** — add an `Owner Project` link column and make the subscriptions count
a link:

```html
<!-- owner project cell -->
<td>
  ${item.owner_project_url
    ? `<a href="${escapeHtml(item.owner_project_url)}" target="_blank" rel="noopener">${escapeHtml(item.owner_project_name || item.owner_project_id)}</a>`
    : escapeHtml(item.owner_project_name || item.owner_project_id || "")}
</td>

<!-- subscriptions count cell -->
<td>
  ${item.listing_url
    ? `<a href="${escapeHtml(item.listing_url)}" target="_blank" rel="noopener">${item.subscriptions ?? 0}</a>`
    : String(item.subscriptions ?? 0)}
</td>
```

### `admin.js`

**`renderStatusRows`**: Add `users_project` and `guests_project` rows to `datazoneItems`; add
`href` from `portal_url` to all three project rows including `owner_project`.

**`renderPackages`**: Update the owner project cell to use `item.owner_project_url` as an `<a>`
(same guard pattern as the existing `listing_url` link). Update the subscriptions cell to wrap the
count in an `<a href="${item.listing_url}">` when `listing_url` is present.

**Replace `renderPrincipals` with `renderProjects`**:

- Group `state.accessGraph.principals` by `datazone_project_id` into three buckets
- Write each bucket into `#tier-owner-body` / `tier-users-body` / `tier-guests-body`
- Row columns: Principal ID (with scope chips), Last token issued, Delete button
- Project column is gone (implied by the container)
- Update summary meta spans: `#tier-owner-meta`, `#tier-users-meta`, `#tier-guests-meta`
- If the same principal appears in multiple buckets, show them in each bucket. That is the actual
  membership model, not duplicate noise.

**Remove `renderAccessTable`**: No longer called; `section-access` is removed from the DOM.

**Per-project add forms**: `createPrincipal` must be form-local, not global. Each project form
contains its own principal input and status node, for example:

```html
<form class="project-principal-form" data-tier="owner">
  <input name="principal" />
  <button type="submit">Add member</button>
</form>
<div class="inline-status" data-role="principal-form-status"></div>
```

Implementation notes:

- `createPrincipal` reads `event.currentTarget.dataset.tier` to derive the scope mode
- Read the principal from `event.currentTarget.elements.principal`
- Write status to the nearest `[data-role="principal-form-status"]`
- Register the submit handler on all `.project-principal-form` elements

Do not reuse one global `principal-input` / `principal-form-status` pair across three forms.

**`renderPrincipalSelectors`**: Remove `"revoke-principal-select"` from the ID list. Populate the
header selector from a deduplicated principal list, but keep principal filtering semantics based on
all memberships for that principal.

**`bindEvents`**: Remove `revoke-refresh` and `revoke-delete` event listeners.

**Header health chip click**: Change scroll target from `#column-structure` to `#section-stack`
(now in column 2).

**`loadAccessGraph`**: Replace `renderPrincipals()` call with `renderProjects()`; remove
`renderAccessTable()` call.

**Package Listings → RALE integration**: Make the listings table an active launcher for RALE Step 1.

- Add a `Use in RALE` control on each listing row, or make the package-name cell selectable in-app
  in addition to the console link
- Selecting a listing sets `state.selectedPackage`
- Sync that selection into `#rale-package-select`
- Trigger the same package-file load path used by the existing RALE package dropdown
- Scroll or focus to `#section-rale` after selection so the workflow continues naturally

Keep the external `listing_url` as a console link. The in-app RALE selection must be a separate
control so the row supports both console navigation and flow selection.

### Tests

**`tests/unit/test_control_plane_router.py`**:

- Assert `GET /admin/structure` response includes `users_project` and `guests_project` with
  `id`, `name`, `status`, `portal_url`
- Assert `GET /access-graph` (or equivalent) includes `owner_project_url` on each listing row
- Assert `GET /principals` preserves multiple rows when a principal belongs to two projects
- If `principal_summary` is added, assert it contains one summary entry with both project IDs

**`tests/integration/test_admin_ui.py`**:

- Update DOM assertions that reference `section-principals` (now `section-projects`)
- Update any assertion that expects `section-stack` inside column 1 (now in column 2)
- Update any assertion that expects a three-column layout (now two columns)
- Remove assertions for `section-access` (column removed)
- Assert `section-stack` appears after `section-failures` in document order
- Assert selecting a package from Package Listings populates RALE Step 1 with that package

---

## What does not change

- The RALE Flow steps (Select / Authorize / Deliver) — unchanged
- The Failure Tests section — unchanged
- The hard-revocation Secret Rotation button — unchanged
- The global principal selector in the header — unchanged
- All API endpoint paths — unchanged
- Existing `/principals` membership rows remain unchanged; any deduplicated principal summary is
  additive only
- The Package Listings table content — `listing_url` and `subscriptions` count already exist in
  the API response; only the rendering adds links
- The delegated `.delete-principal` click handler in `bindEvents` — the same class and
  `data-principal` attribute are reused in the project member rows

---

## Implementation order

1. `control_plane.py` — add `_console_project_url`, extend structure response, add
   `owner_project_url` to listing rows, keep `/principals` membership rows intact, optionally add
   additive `principal_summary`
2. Unit tests — verify new structure shape, `owner_project_url` presence, multi-project membership
   preservation, optional summary shape
3. `admin.html` — restructure to two columns; reorder column 2 sections; remove Data Graph column
4. `admin.js` — `renderProjects`, `renderPackages` link updates, `renderStatusRows` update,
   remove `renderAccessTable`, event binding cleanup
5. Integration tests — update DOM assertions

---

## Acceptance criteria

- Layout is two columns (old Column 2 / Data Graph is gone; no `section-access` in DOM)
- Each project card (Owner / Users / Guests) shows its project ID with a working DataZone console link
- Adding a principal from a project's form correctly places them in that project
- A principal who belongs to multiple projects is shown in each relevant project card, while the
  header principal selector lists them once
- A member can be deleted inline; there is no second delete path in the Revocation section
- Package Listings appear at the top of column 2 and serve as the selection target for RALE Flow
- Each listing row: package name links to listing, owner project name links to its project console
  page, subscriptions count links to the listing page
- Selecting a package from Package Listings updates RALE Step 1 in-app without navigating away
- Column 2 section order top-to-bottom: Listings → RALE Flow → Failure Tests → Stack Health →
  Secret Rotation
- Stack health appears below Failure Tests with the same component rows and status badges
- `./poe check` passes (ruff + mypy)
- `./poe test-unit` passes
