# Domain Swap Plan: DataZone V1 to SageMaker Unified Studio V2

## Goal

Rebuild the RAJA stack so the domain is created as a fresh SageMaker Unified Studio V2 domain from
day one, instead of provisioning a legacy DataZone V1 domain and relying on a later console
upgrade.

This document is intentionally design-only. It identifies tasks, repo locations, rollout order,
and design decisions. It does not prescribe implementation code.

---

## Current state

- The stack currently provisions `aws_datazone_domain.raja` and related projects and asset types in
  `infra/terraform/main.tf`.
- The deployed domain has been observed as `domain_version = "V1"`.
- User-facing copy across the repo still describes the system primarily as "DataZone", even though
  AWS now presents the product as SageMaker Unified Studio.
- Core service integration still uses DataZone APIs and boto3 clients, which is expected because
  Unified Studio continues to surface these domain APIs through the DataZone service namespace.

This means the real problem is not "replace DataZone APIs with SageMaker APIs everywhere". The real
problem is:

1. Provision a V2 domain at creation time.
2. Add any V2-only domain prerequisites to infrastructure.
3. Align naming, docs, tests, and admin UX with SageMaker Unified Studio as the product surface.

---

## Non-goals

- Do not redesign the authorization model. The package-grant model remains `project -> package`.
- Do not replace the existing DataZone-backed service layer purely for naming reasons unless that
  refactor materially improves clarity.
- Do not attempt in-place console upgrade automation for old domains. The clean path is
  destroy/recreate with a V2 domain.

---

## Guiding decisions

### 1. Treat this as a fresh-domain cutover, not an upgrade workflow

Because the user wants a clean, self-contained stack, the plan should assume:

- old domain and projects are disposable
- new deployments create a V2 domain directly
- Terraform is the source of truth for the new stack

### 2. Separate API reality from product naming

AWS product naming has moved to SageMaker Unified Studio, but the Terraform resources and boto3
service namespace remain DataZone-oriented.

Design implication:

- Infrastructure and client integration may continue using `aws_datazone_*` resources and
  `boto3.client("datazone")`
- User-facing copy, admin UI labels, docs, and specs should shift to "SageMaker Unified Studio"
  where that is what users actually see in the AWS console

### 3. Prefer minimal semantic churn in code

There are two viable naming strategies:

- Minimal-change strategy: keep module and env names as `datazone_*`, but rewrite user-facing copy
  to reference SageMaker Unified Studio
- Cleanup strategy: introduce neutral names such as `domain_*` or `studio_*` at the boundary, with
  compatibility aliases for existing `DATAZONE_*` environment variables

Recommendation: implement the minimal-change strategy first, then evaluate whether a naming cleanup
is worth the churn.

---

## Required workstreams

## Workstream 1: Terraform domain definition

### Objective

Ensure new deployments create a SageMaker Unified Studio V2 domain directly.

### Tasks

- Update the domain resource in `infra/terraform/main.tf` to declare a V2 domain explicitly.
- Add any V2-required role wiring for the domain resource, including the domain service role if
  required by the provider and AWS account setup.
- Review all domain-adjacent lifecycle settings for compatibility with destroy/recreate workflows.
- Revisit `prevent_destroy` and other lifecycle guardrails that block intentional teardown.

### Files to inspect

- `infra/terraform/main.tf`
- `infra/terraform/variables.tf`
- `infra/terraform/outputs.tf`
- `infra/terraform/versions.tf`
- `infra/terraform/README.md`

### Design points

- Decide whether the V2-specific role names remain DataZone-flavored or should be renamed to match
  SageMaker Unified Studio terminology.
- Decide whether destroy guardrails should be permanent, conditional by environment, or removed.
- Decide whether the domain resource should surface `domain_version` as a configurable variable or
  hard-code `V2`.

Recommendation: hard-code V2 unless there is a real need to support V1 and V2 side by side.

---

## Workstream 2: IAM and AWS prerequisites

### Objective

Ensure Terraform provisions all roles and permissions required by a V2 domain.

### Tasks

- Identify the exact IAM roles and managed policies required for a fresh Unified Studio V2 domain.
- Add or update Terraform resources for those roles.
- Verify Lambda, ECS, and control-plane permissions still cover the same domain operations after the
  V2 cutover.
- Confirm whether additional SageMaker-side permissions are required beyond the existing DataZone
  permissions.

### Files to inspect

- `infra/terraform/main.tf`
- `.env` consumers in Poe tasks via `pyproject.toml`
- any deployment notes in `infra/terraform/README.md`

### Design points

- Decide whether V2 role requirements should be documented as account prerequisites or fully managed
  in Terraform.
- Decide whether to preserve existing role names for continuity or rename them to reflect the new
  product surface.

Recommendation: provision as much as possible in Terraform and document the remainder explicitly.

---

## Workstream 3: Environment contract and outputs

### Objective

Keep runtime configuration coherent after the domain swap.

### Tasks

- Review all environment variables that assume `DATAZONE_*` naming.
- Decide whether to keep those names for compatibility or introduce aliases.
- Ensure Terraform outputs still expose the domain ID, portal URL, project IDs, asset type, and
  revision needed by scripts and integration tests.
- Update any output-display tooling to use SageMaker Unified Studio language where appropriate.

### Files to inspect

- `infra/terraform/outputs.tf`
- `infra/tf-outputs.json`
- `scripts/show_outputs.py`
- `scripts/seed_test_data.py`
- `scripts/seed_packages.py`
- `src/raja/datazone/service.py`
- Lambda environment blocks in `infra/terraform/main.tf`

### Design points

- Keep `DATAZONE_*` variables unchanged for compatibility, or add `STUDIO_*` or `DOMAIN_*` aliases.
- Decide whether `tf-outputs.json` keys remain stable or are renamed.

Recommendation: keep current output and env keys stable for the first V2 rebuild, but update human
readable labels and docs.

---

## Workstream 4: Runtime service integration

### Objective

Verify that the runtime behavior is still valid against a V2 domain and decide what, if anything,
should be renamed.

### Tasks

- Review the `src/raja/datazone/` package for assumptions that are V1-specific.
- Verify project creation, listing search, asset-type access, and subscription workflows still
  match the V2 domain model.
- Review all explicit `boto3.client("datazone")` usage and confirm it remains correct.
- Identify any user-facing error messages or logs that should reference SageMaker Unified Studio
  instead of DataZone.

### Files to inspect

- `src/raja/datazone/service.py`
- `src/raja/datazone/__init__.py`
- `src/raja/server/dependencies.py`
- `src/raja/server/routers/control_plane.py`
- `lambda_handlers/rale_authorizer/handler.py`

### Design points

- Keep the package name `raja.datazone`, or introduce a neutral façade such as `raja.domain`.
- Decide whether the control plane should expose "domain" terminology instead of "datazone"
  terminology in JSON payloads.

Recommendation: keep internal service/module names for now; change user-facing payload labels only
if the admin UX benefits materially.

---

## Workstream 5: Admin UI and control-plane terminology

### Objective

Make the product language in the admin surface match what users see in AWS.

### Tasks

- Audit all admin UI labels, summaries, and section headings that currently say "DataZone".
- Update the Domain Structure column so the top-level concept is SageMaker Unified Studio, with the
  domain as the root object.
- Review API responses that feed the admin UI and decide whether field names need to change or only
  display labels.
- Ensure links point users to the right console destination and wording.

### Files to inspect

- `src/raja/server/templates/admin.html`
- `src/raja/server/static/admin.js`
- `src/raja/server/app.py`
- `src/raja/server/routers/control_plane.py`
- `specs/49-domain-ux/01-admin-ng.md`

### Design points

- Whether to rename the "DataZone panel" in the spec and UI to "Studio Domain" or "Unified Studio".
- Whether API payload shape changes are worth the compatibility cost.

Recommendation: change display language first; defer API field renames unless they are actively
confusing.

---

## Workstream 6: Tests

### Objective

Make the test suite validate the V2 deployment shape and updated terminology without adding brittle
console-level assumptions.

### Tasks

- Update integration tests that assume old wording or old domain metadata.
- Add explicit assertions that the deployed domain is V2 if the AWS provider exposes that attribute.
- Review admin UI tests for text and structure changes after terminology updates.
- Review unit tests that mock DataZone service responses for user-facing copy assumptions.

### Files to inspect

- `tests/integration/test_admin_ui.py`
- `tests/integration/test_control_plane.py`
- `tests/integration/test_failure_modes.py`
- `tests/unit/test_control_plane_router.py`
- `tests/unit/test_server_app.py`
- `tests/unit/test_datazone_service.py`

### Design points

- Whether to assert `domain_version == "V2"` directly in live integration tests or only via a
  health/status payload.
- Whether to rename tests from "datazone" to "domain" or "studio" terms.

Recommendation: add a direct live assertion for V2 if it is cheaply observable.

---

## Workstream 7: Seed, cleanup, and destroy workflows

### Objective

Make the stack reproducible, clean to tear down, and safe to rebuild.

### Tasks

- Audit seed scripts for assumptions about pre-existing projects, listings, or asset types.
- Add or document cleanup steps for generated DataZone resources outside Terraform state.
- Review destroy behavior for lingering projects, memberships, subscriptions, listings, and assets.
- Ensure the documented clean rebuild path is deterministic.

### Files to inspect

- `scripts/seed_test_data.py`
- `scripts/seed_packages.py`
- `scripts/cleanup_datazone_projects.py`
- `pyproject.toml`
- `infra/terraform/main.tf`

### Design points

- Whether cleanup should be best-effort scripting or fully automated in Terraform lifecycle hooks.
- Whether seed scripts should stay idempotent but append-only, or should gain a reset mode.

Recommendation: keep seeding idempotent and add explicit cleanup tooling rather than hiding cleanup
inside deploy logic.

---

## Workstream 8: Documentation and repo narrative

### Objective

Bring the project docs in line with the actual product boundary the user is buying and seeing.

### Tasks

- Update the root project docs to explain that the authorization backend is backed by SageMaker
  Unified Studio domains via DataZone APIs.
- Reconcile old "Use SageMaker" specs with current implementation language.
- Update deployment and destroy instructions to describe the fresh V2-domain flow.
- Update changelog entries or release notes if this becomes a visible migration milestone.

### Files to inspect

- `AGENTS.md`
- `CHANGELOG.md`
- `infra/terraform/README.md`
- `specs/48-use-sagemaker/01-sm-ticket.md`
- `specs/48-use-sagemaker/02-sm-setup.md`
- this file: `specs/49-domain-ux/02-domain-swap.md`

### Design points

- Whether to describe the backend as "SageMaker Unified Studio" everywhere or as "DataZone /
  SageMaker Unified Studio" until the naming transition settles.

Recommendation: use "SageMaker Unified Studio" in headings and user-facing narrative, and mention
the DataZone API/resource names in implementation notes.

---

## Rollout order

### Phase 1: Infrastructure readiness

- Finalize the V2 domain shape in Terraform.
- Add required IAM roles and policies.
- Remove or relax destroy blockers that prevent intentional rebuilds.
- Validate a clean `deploy -> destroy -> deploy` cycle.

### Phase 2: Runtime validation

- Verify runtime APIs behave correctly against the fresh V2 domain.
- Confirm seed scripts and control-plane flows still work.
- Confirm admin structure APIs report the right domain metadata.

### Phase 3: Naming cleanup

- Update admin UI copy and docs.
- Update tests that validate wording or visible structure.
- Decide whether any payload or environment-name cleanup is still justified.

---

## Acceptance criteria

- A brand-new deployment produces a domain that is V2 at creation time.
- The stack can be destroyed intentionally without hidden manual steps.
- Seed scripts succeed against the fresh V2 domain.
- RALE authorization flow still works end to end.
- Admin UI reflects SageMaker Unified Studio terminology where users interact with the system.
- Documentation explains clearly why Terraform and boto3 still use DataZone resource names under a
  SageMaker Unified Studio product surface.

---

## Open questions

- What exact managed policies and trust relationships are required for the V2 domain service role?
- Does the provider expose enough V2 metadata to assert the domain version directly in tests?
- Should we preserve the `DATAZONE_*` environment contract indefinitely, or start introducing
  neutral aliases now?
- Is there any V2-specific domain behavior that changes listing, asset-type, or subscription flows?
- Should JSON payloads returned by the control plane continue using `datazone` keys, or should the
  rename be limited to visible labels and docs?

---

## Recommendation summary

- Rebuild with a fresh V2 domain instead of upgrading old domains.
- Keep DataZone API/resource names internally where AWS still requires them.
- Update Terraform first, then validate runtime behavior, then clean up docs and UX language.
- Avoid broad renames unless they reduce real confusion; the required migration is infrastructural
  first, cosmetic second.
