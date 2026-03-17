# 07 Environment Fix: Realistic Path Forward

## What went wrong

`05-blueprint.md` assumed three things that are false on the current deployed stack:

1. `"CustomAWS"` is a named built-in blueprint identifier — it is not. No such ID exists in any
   DataZone domain.
2. Environment profiles (`CreateEnvironmentProfile`) are available — they are not. On V2 domains
   this API returns `API not supported for domain version`.
3. The custom blueprint is the right model for "bring your own bucket" — it is not available as a
   managed V2 primitive.

The root confusion: DataZone documentation conflates "Custom AWS Service blueprint" (a thing you
create via a multi-step console-only flow in V1) with a stable API identifier. There is no named
built-in `CustomAWS` blueprint that can be referenced via `PutEnvironmentBlueprintConfiguration`.

**However: the environment API itself is NOT blocked.**

`list_environments` works fine on this domain. The gap analysis correctly observes that
`CreateEnvironment` returned `Invalid authorization request` — but that is a *caller permissions*
error, not an `API not supported for domain version` error. The environments are missing because
no code ever created them, not because the domain version forbids it.

---

## What actually is available

### Managed blueprints

The `list_environment_blueprints` call in `sagemaker_gaps.py` was issued with `managed=False`,
looking for a custom blueprint. The domain has none. The correct call is `managed=True`, which
returns the AWS-managed blueprints that ARE available:

| Blueprint name | Creates | Sets physicalEndpoints? |
| --- | --- | --- |
| `DefaultDataLake` | S3 bucket, Glue DB, Athena workgroup | Yes |
| `AmazonBedrockStudio` | Bedrock agent/model resources | No (not S3-backed) |
| `SparkEmrServerless` | EMR Serverless app | No |

`DefaultDataLake` is the only managed blueprint that produces the `physicalEndpoints` object
that DataZone's file browser requires. In V2 domains, it creates a **DataZone-managed S3
bucket** — which is the same tradeoff that `05-blueprint.md` rejected. But it is the only path
that uses the DataZone environment model at all.

### Terraform resource schema (AWS provider ≥ 5.72)

`aws_datazone_environment` does NOT require an environment profile:

```hcl
data "aws_datazone_environment_blueprint" "default_data_lake" {
  domain_id = aws_datazone_domain.raja.id
  name      = "DefaultDataLake"
  managed   = true
}

resource "aws_datazone_environment_blueprint_configuration" "default_data_lake" {
  domain_id                 = aws_datazone_domain.raja.id
  environment_blueprint_id  = data.aws_datazone_environment_blueprint.default_data_lake.id
  enabled_regions           = [var.aws_region]
  manage_access_role_arn    = aws_iam_role.datazone_environment_owner.arn
  provisioning_role_arn     = aws_iam_role.datazone_environment_owner.arn
}

resource "aws_datazone_environment" "owner" {
  domain_identifier               = aws_datazone_domain.raja.id
  project_identifier              = aws_datazone_project.owner.id
  environment_blueprint_identifier = data.aws_datazone_environment_blueprint.default_data_lake.id
  name                            = "raja-owner-env"
  account_identifier              = data.aws_caller_identity.current.account_id
  region                          = var.aws_region
  depends_on                      = [aws_datazone_environment_blueprint_configuration.default_data_lake]
}
```

No `environment_profile_identifier` needed. The profile step is skipped entirely.

### CreateEnvironment auth error

The `Invalid authorization request` error in `sagemaker_gaps.py` occurred because the script
caller (the deployment machine/CI role) was not added as a DataZone domain owner with
`CreateEnvironment` privilege. This is a DataZone permission model issue, not a V2 restriction.

Terraform applies using the same AWS caller identity that owns the domain resources. That caller
can always perform `datazone:CreateEnvironment` because Terraform runs with admin-level
credentials. The error will not recur in Terraform.

---

## Revised tradeoff: DefaultDataLake vs. RAJA-controlled browsing

### Option A: DefaultDataLake environments in Terraform

**What it does:**

- Creates a DataZone-managed S3 bucket for each project (owner, users, guests) in addition to
  `rajee_registry`.
- Sets `physicalEndpoints` → file browser in the SageMaker Unified Studio portal works.
- Environment IDs become real Terraform outputs; `DATAZONE_*_ENVIRONMENT_ID` variables are
  populated.
- Subscription targets become possible (DataZone can resolve them against the managed bucket).

**What it does NOT do:**

- Does not point `physicalEndpoints` at `rajee_registry`. The DataZone file browser shows the
  new managed bucket, not the Quilt package registry.
- Does not eliminate the split data model concern from `05-blueprint.md`. Quilt packages are
  in `rajee_registry`; DataZone files are in the managed bucket.

**Mitigation for split model:**

After environment creation, register `rajee_registry` as a DataZone data source on the domain.
DataZone's catalog layer indexes the registry; the environment's managed bucket is used only
for DataZone's own internal tooling (notebooks, Athena queries). Users who want to browse
Quilt packages use the Quilt catalog, not the DataZone file tab.

**Acceptance criteria:**

- DataZone project overview "Failed to fetch folder" error is gone.
- `GET /admin/structure` returns non-empty `environment_id` for all three projects.
- `./poe test-integration` passes.

### Option B: Remove DataZone file browser dependency

**What it does:**

- Accept that the DataZone project overview file browser will remain broken or empty.
- Do not create DataZone environments.
- Remove the `DATAZONE_*_ENVIRONMENT_ID` variables (they have no values and no callers that
  depend on them today).
- Add a link to the Quilt catalog in the admin UI for each project (`registry_url` field in
  `GET /admin/structure`).

**What it eliminates:**

- All DataZone environment provisioning complexity.
- The split-bucket model concern.
- The IAM environment roles (which currently exist but serve no purpose without environments).

**Acceptance criteria:**

- Admin UI shows a working link to the Quilt catalog per project.
- `GET /admin/structure` includes `registry_url` for each project.
- `datazone_*_environment_id` outputs are removed from `outputs.tf`.
- `DATAZONE_*_ENVIRONMENT_ID` variables removed from Lambda config.

---

## Recommended path

**Start with Option B, keep Option A as a follow-on.**

Rationale:

1. Option B is fully implementable today with no external dependencies or API uncertainty.
2. The DataZone file browser, even when fixed by Option A, would show a DataZone-managed bucket
   that users cannot use for Quilt package browsing. It is a cosmetic fix for a UI surface that
   most RAJA users do not access directly.
3. Option A can be added later (after confirming `DefaultDataLake` behaves correctly on this
   account) once there is a concrete use case for DataZone-managed storage.
4. If the DataZone file browser in the portal matters for stakeholders, Option A can be done in
   a separate ticket scoped only to environment provisioning, with no impact on the existing
   RAJA authorization flow.

---

## Implementation plan for Option B

### 1. Add `registry_url` to admin structure

In [src/raja/server/routers/control_plane.py](src/raja/server/routers/control_plane.py),
add a `registry_url` field alongside `environment_id` in each project block. The URL is the
Quilt catalog URL for the `rajee_registry` bucket.

The value can be a `REGISTRY_CATALOG_URL` environment variable (defaulting to empty string),
or derived from the existing `RAJEE_ENDPOINT` variable.

### 2. Remove dead environment variables

Remove `DATAZONE_OWNER_ENVIRONMENT_ID`, `DATAZONE_USERS_ENVIRONMENT_ID`, and
`DATAZONE_GUESTS_ENVIRONMENT_ID` from:

- [infra/terraform/main.tf](infra/terraform/main.tf) Lambda `environment` block
- [infra/terraform/variables.tf](infra/terraform/variables.tf) input variable declarations
- [infra/terraform/outputs.tf](infra/terraform/outputs.tf) output declarations

These variables have been empty strings since the stack was first deployed. No Lambda code
reads them today.

### 3. Remove dead IAM roles (optional, low priority)

`raja-dz-env-owner`, `raja-dz-env-users`, `raja-dz-env-guests` currently exist but have no
consumers. If Option A is expected to follow, keep them. If the environment model is being
dropped entirely, remove them.

### 4. Update sagemaker_gaps.py

Remove or gate the environment provisioning path. The script should no longer attempt
`_ensure_environments`. Discovery behavior (`_discover_environment_ids`) can stay as a no-op
that always returns empty.

### 5. Update admin UI copy

In [src/raja/server/templates/admin.html](src/raja/server/templates/admin.html), replace
the empty environment URL links with the new `registry_url` field from the structure API.
Link text: "Open in Quilt catalog".

---

## Implementation plan for Option A (if chosen later)

### Prerequisites

Before writing any Terraform, verify these against the live domain:

```bash
# Confirm DefaultDataLake is available
aws datazone list-environment-blueprints \
  --domain-identifier dzd-45tgjtqytva0rr \
  --managed \
  --region us-east-1 \
  --query 'items[].name'

# Confirm blueprint configuration API works (it's separate from environment profile API)
aws datazone list-environment-blueprint-configurations \
  --domain-identifier dzd-45tgjtqytva0rr \
  --region us-east-1
```

If `DefaultDataLake` is in the response, proceed. If not, Option A is also blocked and Option B
is the only viable path.

### Terraform changes

1. Add `data "aws_datazone_environment_blueprint" "default_data_lake"` lookup.
2. Add `aws_datazone_environment_blueprint_configuration` to enable the blueprint on the domain.
3. Add three `aws_datazone_environment` resources — one per project — using the blueprint ID
   directly (no profile).
4. Replace `var.datazone_owner_environment_id` etc. with references to the new environment
   resource IDs.
5. Remove the three `datazone_*_environment_id` input variables (they become outputs, not inputs).

### IAM changes

The `DefaultDataLake` blueprint requires two IAM roles on
`aws_datazone_environment_blueprint_configuration`:

- `manage_access_role_arn` — DataZone uses this to manage IAM permissions for the environment.
- `provisioning_role_arn` — DataZone uses this to provision S3/Glue resources.

These can use the existing `raja-dz-env-owner` role if its trust policy includes
`datazone.amazonaws.com` with `sts:AssumeRole`. Confirm the trust policy allows both `AssumeRole`
and `TagSession` (required by DataZone provisioning).

### Acceptance criteria

- `terraform apply` completes without error.
- Three DataZone environments appear in the SageMaker Unified Studio portal with status `Active`.
- DataZone project overview no longer shows "Failed to fetch folder".
- `GET /admin/structure` returns non-empty `environment_id` for all three projects.
- `./poe test-integration` passes.

---

## Status

`07-environment-fix.md` status: **plan**

Next action: implement Option B. Ticket for Option A to be created separately if/when the
DataZone file browser becomes a stakeholder requirement.
