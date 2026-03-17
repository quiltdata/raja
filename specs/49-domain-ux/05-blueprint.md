# RAJA DataZone Environment Blueprint

## Problem

The DataZone project UI unconditionally attempts to resolve `physicalEndpoints` on whatever S3
location backs the project. RAJA creates three DataZone projects (owner / users / guests) via
Terraform but never provisions an environment for any of them. No environment → no S3 location →
`e.physicalEndpoints` is `undefined` → the file browser throws and the project overview page is
broken.

---

## Why not DefaultDataLake?

`DefaultDataLake` always creates a new, DataZone-managed S3 bucket. There is no way to point it
at an existing bucket in the same AWS account. RAJA already has `rajee_registry` — the Quilt
package registry bucket that is the actual data store. Creating a second bucket that DataZone
manages would split the data model without benefit.

---

## Correct approach: custom AWS service blueprint

DataZone's **custom AWS service blueprint** is designed exactly for this case: integrate existing
AWS resources with DataZone without provisioning anything new. The blueprint:

- Does **not** create S3 buckets, Glue databases, or access points
- Uses a "bring your own role" IAM model
- Accepts S3 action links to provide DataZone's native S3 browser interface
- Sets `physicalEndpoints` to the existing S3 bucket path via the provided IAM role

This means `physicalEndpoints` for each RAJA project environment will point at `rajee_registry`,
and the DataZone file browser will actually work — showing real Quilt package files.

---

## Design

```
DataZone Domain
└── Blueprint: "RAJA" (custom AWS service blueprint)
    └── EnvironmentProfile: "RAJA registry"
        ├── Environment: raja-owner-env
        │     endpoints: s3://rajee_registry/  (packages)
        │                s3://rajee_test/       (data)
        │     role: raja-dz-env-owner
        ├── Environment: raja-users-env
        │     endpoints: s3://rajee_registry/
        │                s3://rajee_test/
        │     role: raja-dz-env-users
        └── Environment: raja-guests-env
              endpoints: s3://rajee_registry/
                         s3://rajee_test/
              role: raja-dz-env-guests
```

Each environment exposes both the Quilt package registry (`rajee_registry`) and the data bucket
(`rajee_test`). The blueprint is shared; the IAM role varies per tier and enforces what each
tier can do within those buckets.

---

## Decision points

### 1. One IAM role or three?

Each environment needs an IAM role that DataZone uses to access the registry bucket. Options:

- **One role per project tier** — owner role has full bucket access, users role has read/write,
  guests role has read-only. Scopes match the existing RAJA tier model. Mirrors the authorization
  model already expressed in RAJA's Cedar policies.
- **One shared role** — simpler Terraform, but loses the per-tier S3 access differentiation.
  DataZone's browser would show the same view regardless of which project you're in.

**Recommended:** One role per tier. The registry bucket's IAM policy can enforce prefix or action
restrictions per role, consistent with RAJA's scope model (`*:*:*`, `S3Object:*:*`,
`S3Object:*:s3:GetObject`).

### 2. S3 path scope per environment

Each environment registers two S3 action links: `s3://rajee_registry/` (packages) and
`s3://rajee_test/` (data). Both are at bucket root — no per-tier prefixes. Access enforcement
is handled by the IAM role attached to the environment, not by path restrictions. Per-tier path
prefixes would require restructuring the registry layout and are not needed.

### 3. Subscription targets

DataZone requires a `SubscriptionTarget` on each environment for subscription grants to resolve —
the target maps an asset type (`QuiltPackage`) to the physical endpoint on the subscriber's
environment.

For the custom service blueprint, subscription targets describe how a subscriber's environment
receives access to a published listing. Since RAJA manages subscription acceptance itself (via
`accept_subscription_request` in `datazone/service.py`), and delivery goes through RALE/TAJ rather
than DataZone's native access machinery, subscription targets may be either:

- **Required for UI completeness** — DataZone may show errors or incomplete state in the
  subscription grant UI if no target is registered.
- **Not required for RAJA's auth flow** — TAJ-based delivery bypasses DataZone's access machinery
  entirely.

**Open question:** Confirm whether the project overview error is fully resolved by the environment
alone, or whether a subscription target is also needed. Defer subscription target implementation
until tested.

### 4. Terraform provisioning

All three environments are static — the same registry bucket, same three projects. Options:

- **Terraform-only** — `aws_datazone_environment` resources declared alongside existing project
  resources. Environment IDs become Terraform outputs. Straightforward.
- **Terraform + post-apply script** — if the custom blueprint provisioning step requires manual
  DataZone console actions (some blueprint types require clicking through the console to activate),
  a script may be needed to complete setup.

**Recommended:** Terraform-only. Custom service blueprints are designed to be fully API-driven.
Confirm during implementation that no console step is required.

---

## Tasks

### IAM

1. **Create three IAM roles** — one per project tier — for DataZone environment access.
   All roles apply to both `rajee_registry` and `rajee_test`:
   - `raja-dz-env-owner` — full access (`s3:*`)
   - `raja-dz-env-users` — read/write (`s3:GetObject`, `s3:PutObject`, `s3:ListBucket`)
   - `raja-dz-env-guests` — read-only (`s3:GetObject`, `s3:ListBucket`)

   Each role's trust policy allows DataZone to assume it (principal: `datazone.amazonaws.com`).

### Terraform

1. **Register the custom blueprint on the domain** via
   `aws_datazone_environment_blueprint_configuration`. Blueprint name: `"CustomAWS"` (this is the
   built-in identifier for custom AWS service blueprints). Attach it to the RAJA domain with the
   region enabled.

2. **Create one environment profile** (`aws_datazone_environment_profile`) named
   `"RAJA registry"`. Profile references the custom blueprint and registers two S3 action links:
   `s3://{rajee_registry_bucket_name}/` and `s3://{rajee_test_bucket_name}/`.

3. **Create one environment per project** (`aws_datazone_environment`):
   - `raja-owner-env` → owner project, `environment_role_arn` = `raja-dz-env-owner`
   - `raja-users-env` → users project, `environment_role_arn` = `raja-dz-env-users`
   - `raja-guests-env` → guests project, `environment_role_arn` = `raja-dz-env-guests`

4. **Add environment IDs to Terraform outputs.**

5. **Create subscription targets** per environment if confirmed necessary (see Decision 3).

### Control plane

1. **Surface environment IDs in `GET /admin/structure`** — add `environment_id` and
   `environment_url` to each project block in the structure response.

2. **Add `_console_environment_url` helper** alongside `_console_project_url` and
   `_console_domain_url` in `control_plane.py`.

---

## Verification

- Reload the DataZone project overview for `raja-owner`. "Failed to fetch folder" error is gone.
  File browser shows contents of `rajee_registry`.
- Each project shows its environment in the DataZone console with status `Active`.
- `GET /admin/structure` returns `environment_id` and `environment_url` for all three projects.
- Guests environment role cannot write to the registry bucket (IAM deny test).
- `./poe test-unit` passes.
- `./poe test-integration` passes.
