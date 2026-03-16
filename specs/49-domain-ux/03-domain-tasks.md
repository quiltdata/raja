# Domain Swap: Verified Task Checklist

Derived from direct inspection of the codebase and AWS documentation research.
Assumes Path A: creating a fresh V2 domain via Terraform (not upgrading an existing V1 domain).

---

## Research findings (resolved questions)

| Question | Answer |
| --- | --- |
| Terraform `domain_version` support | Supported. `aws_datazone_domain` accepts `domain_version = "V2"`. `service_role` is required when V2. |
| V2 project/workspace API compatibility | Same DataZone project APIs. `aws_datazone_project`, membership, asset-type, and subscription APIs are unchanged. "Spaces" are per-user compute sandboxes within a project, not a project replacement. |
| Subscription model in V2 | Preserved. `CreateSubscriptionRequest` → `AcceptSubscriptionRequest` flow is unchanged. V2 adds auto-fulfillment for managed AWS assets but does not replace the manual subscription path RAJA uses. |
| `AmazonDataZoneDomainExecutionRolePolicy` sufficient for V2? | **No.** V2 requires two new roles. See section 1b below. |
| `DATAZONE_*` env var names | No V2-mandated rename. Keep them. |

---

## Gotchas

These are the ways a Path A (fresh V2) deployment can fail silently or require manual
recovery. Verified against AWS docs at
[upgrade-domain](https://docs.aws.amazon.com/sagemaker-unified-studio/latest/adminguide/upgrade-domain.html)

**G1. `domain_version` forces resource replacement.**
Terraform treats any change to `domain_version` as requiring destroy + recreate of
`aws_datazone_domain`. Run `terraform plan` before every apply that touches the domain
resource and confirm the replacement is intentional.

**G2. `skip_deletion_check = true` will block `terraform destroy`.**
The domain resource (`main.tf:222`) and all three project resources (`main.tf:227-246`)
have `skip_deletion_check = true`. Verify destroy succeeds end-to-end on a non-production
stack before relying on it in the V2 rebuild cycle.

**G3. Projects must be emptied before the domain can be destroyed.**
DataZone rejects domain deletion if projects have active subscriptions, memberships, or
assets. `scripts/cleanup_datazone_projects.py` must succeed *before* `terraform destroy`.
If it is missing coverage for any resource type, the destroy will stall mid-run.

**G4. IAM roles cannot log into the SageMaker Unified Studio portal — confirmed.**
AWS docs state explicitly: "IAM roles cannot log into Amazon SageMaker Unified Studio."
The current deployed domain has `singleSignOn: DISABLED` (confirmed via `aws datazone
get-domain`). This means the portal URL is non-functional for anyone. If web portal
access matters, at least one IAM *user* or IAM Identity Center user must be added as a
root domain owner. API access (Lambda, boto3, admin server) is unaffected.

**G5. Execution role trust policy needs `sts:SetContext` for V2 — confirmed.**
V1 trust policy allows `sts:AssumeRole` and `sts:TagSession`. V2 adds `sts:SetContext`.
Attaching `SageMakerStudioDomainExecutionRolePolicy` alone is not enough if the trust
document was copied from the V1 role without adding `sts:SetContext`.

**G6. Pin the Terraform AWS provider version.**
The `domain_version` and `service_role` arguments were added in a recent provider release.
Pin to ≥ 5.72 in `infra/terraform/versions.tf` and run `terraform init -upgrade` before
applying to avoid a schema mismatch error.

**G7. Environments / environment profiles do not exist in V2.**
AWS docs: DataZone Environments are not carried over; they are replaced by SageMaker
project profiles. For Path A this is a non-issue since we never created V1 environments,
but any future work referencing DataZone environment APIs will fail against a V2 domain.

**G8. Rollback is blocked once any project is created via SageMaker Unified Studio.**
AWS docs: rolling back from V2 to V1 is only permitted when no projects were created in
SageMaker Unified Studio. Once `poe seed-test-data` runs and creates projects via the
Unified Studio surface, rollback is no longer available without deleting those projects
first.

**G9. Projects created in the V2 portal are not visible in the legacy DataZone portal.**
Not an issue for RAJA's current setup (no legacy DataZone portal usage), but worth noting
if any human operators use both portals during a transition period.

---

## 1. Terraform

### 1a. Domain resource — `infra/terraform/main.tf:218-225`

- [ ] Add `domain_version = "V2"` to `aws_datazone_domain.raja`.
- [ ] Add `service_role` argument pointing to the new service role ARN (see 1b).
  This argument is **required** when `domain_version = "V2"`; Terraform will error
  without it.
- [ ] Decide whether to remove or make `skip_deletion_check = true` conditional on
  environment, given gotcha G2.

### 1b. Domain IAM roles — `main.tf:196-216` ← **highest priority; two new resources required**

The current execution role uses `AmazonDataZoneDomainExecutionRolePolicy`. V2 requires:

- [ ] Add a new `aws_iam_role` for the **execution role** using managed policy
  `SageMakerStudioDomainExecutionRolePolicy` (replaces `AmazonDataZoneDomainExecutionRolePolicy`).
  Trust principal remains `datazone.amazonaws.com`; add `sts:SetContext` to the trust
  policy actions alongside the existing `sts:AssumeRole` and `sts:TagSession` (gotcha G5).
- [ ] Add a **new** `aws_iam_role` for the **domain service role** (`AmazonSageMakerDomainService`
  equivalent) with managed policy `SageMakerStudioDomainServiceRolePolicy` attached.
  Trust principal is `datazone.amazonaws.com` with `sts:AssumeRole` only (no TagSession or
  SetContext). This role has no V1 equivalent and maps to the `service_role` argument on the
  domain resource.
- [ ] Update `aws_datazone_domain.raja` to reference both new role ARNs.
- [ ] Keep the old V1 execution role in Terraform or remove it — do not leave an
  orphaned IAM role in the account.

### 1c. Lambda IAM policies — `main.tf:301-377`, `main.tf:451-497`, `main.tf:565-597`

Research confirms all 14 DataZone API actions used by the Lambda policies remain valid
on V2. No additions or removals are required by the V2 migration itself.

- [ ] No action needed unless `terraform plan` shows unexpected drift after V2 deploy.

### 1d. Project resources — `main.tf:227-246`

Research confirms `aws_datazone_project` and its APIs are unchanged in V2.

- [ ] Confirm `skip_deletion_check = true` on all three project resources behaves
  correctly during destroy (gotcha G3 applies here).
- [ ] No structural changes to project resources required.

### 1e. Variables and outputs

- [ ] Add a `service_role_arn` output (or similar) for the new domain service role,
  if it needs to be referenced outside Terraform.
- [ ] All seven existing `datazone_*` outputs in `infra/terraform/outputs.tf:6-39`
  remain valid. No renames required.
- [ ] Pin `hashicorp/aws` provider to ≥ 5.72 in `infra/terraform/versions.tf`
  (gotcha G6).

---

## 2. Runtime service integration

Research confirms all DataZone client API signatures, response shapes, and
enum values are unchanged on V2. The items below are verification checkboxes,
not anticipated changes.

- [ ] After first successful V2 deploy, run `poe seed-test-data` and confirm
  `get_user_profile(type="IAM")` (`service.py:207-231`) returns a valid ARN.
- [ ] Confirm `create_project_membership` with `designation="PROJECT_CONTRIBUTOR"`
  (`service.py:315-338`) succeeds against the V2 domain.
- [ ] Confirm `create_subscription_request` + `accept_subscription_request`
  flow (`service.py:340-380`) still completes end-to-end.

---

## 3. Environment contract

No changes required. V2 does not introduce new required env vars.

- [ ] Confirm `scripts/seed_test_data.py:55-70` (`_hydrate_datazone_env`) reads
  the same seven output keys after Terraform apply. No output keys are being
  renamed.

---

## 4. Seed and cleanup scripts

- [ ] Run `scripts/cleanup_datazone_projects.py` against a live V1 stack to
  confirm it handles all resource types before destroy. Fix any failures before
  relying on it in the V2 rebuild cycle (gotcha G3).
- [ ] After V2 deploy, run `poe seed-test-data` and confirm all three tiers
  (`PROJECT_OWNER`, `PROJECT_CONTRIBUTOR`) seed correctly.

---

## 5. Integration tests

### 5a. `tests/integration/test_admin_ui.py`

No test changes are needed before the first V2 deploy. Run the suite against
a live V2 stack and update only assertions that actually fail.

- [ ] After V2 deploy: run `poe test-integration` and record failures.
- [ ] Add one assertion to the existing structure test (lines 159-180) that
  checks `datazone.domain.domain_version == "V2"` if that field is surfaced
  in the admin structure API response.

### 5b. `tests/integration/test_control_plane.py`

- [ ] No anticipated changes. Verify against live V2 stack.

---

## 6. Admin UI copy

Display-only. Do after infrastructure is working.

- [ ] `src/raja/server/templates/admin.html:47` — "DataZone-backed" → "SageMaker Unified Studio–backed".
- [ ] `admin.html:57` — Section heading references "DataZone domain".
- [ ] `admin.html:62` — `<span>DataZone</span>` section label.
- [ ] `src/raja/server/static/admin.js:160,187-208` — Update rendered labels only; do not change JSON key paths unless the API response fields are renamed.

---

## 7. Ordered execution

1. Add new IAM roles to Terraform (section 1b) — do not apply yet.
2. Add `domain_version = "V2"` and `service_role` to domain resource (section 1a).
3. Pin provider version (section 1e).
4. Run `terraform plan` and confirm: one domain replacement, two new IAM roles, no
   other unexpected changes.
5. Run `scripts/cleanup_datazone_projects.py` to clear active V1 resources.
6. Run `terraform destroy` and confirm it completes without manual steps.
7. Run `terraform apply` and confirm the domain is created as V2.
8. Run `poe seed-test-data`.
9. Run `poe test-integration` and fix any failures.
10. Update admin UI copy (section 6).
