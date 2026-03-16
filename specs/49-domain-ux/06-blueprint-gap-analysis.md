# Gap Analysis: 05-blueprint

## Summary

`specs/49-domain-ux/05-blueprint.md` is only partially implemented.

The repo now has:

- Tier-specific IAM roles for DataZone environments
- Control-plane exposure of `environment_id` and `environment_url`
- Integration coverage that asserts those fields exist in `/admin/structure`
- Post-deploy discovery logic that checks whether DataZone environments exist and, if they do,
  can propagate discovered IDs into Lambda configuration

The repo does **not** yet have the core outcome promised by the blueprint:

- No custom DataZone blueprint is registered
- No DataZone environment profile exists
- No per-project DataZone environments exist
- No environment IDs are emitted from Terraform outputs in the deployed stack
- No subscription targets exist

As of the successful `./poe test-all` run on March 16, 2026, the live deploy still reports:

- `Custom blueprint: not found`
- `Environment owner: raja-owner-env (missing)`
- `Environment users: raja-users-env (missing)`
- `Environment guests: raja-guests-env (missing)`

So the implementation is currently best described as: "prepare the stack for environments and
surface environment metadata when available," not "fully provision the blueprint described in
05-blueprint.md."

---

## Implemented

### 1. IAM roles

Implemented in [infra/terraform/main.tf](/Users/ernest/GitHub/raja/infra/terraform/main.tf):

- `raja-dz-env-owner`
- `raja-dz-env-users`
- `raja-dz-env-guests`

These roles match the blueprint's tier split and cover both:

- `rajee_registry`
- `rajee_test`

This closes the IAM portion of the blueprint at the role-definition level.

### 2. Outputs for role ARNs

Implemented in [infra/terraform/outputs.tf](/Users/ernest/GitHub/raja/infra/terraform/outputs.tf):

- `datazone_owner_environment_role_arn`
- `datazone_users_environment_role_arn`
- `datazone_guests_environment_role_arn`

This was not explicitly required by the blueprint, but it is useful for any post-apply
environment provisioning flow.

### 3. Control-plane environment fields

Already present and verified in:

- [src/raja/server/routers/control_plane.py](/Users/ernest/GitHub/raja/src/raja/server/routers/control_plane.py)
- [tests/unit/test_control_plane_router.py](/Users/ernest/GitHub/raja/tests/unit/test_control_plane_router.py)
- [tests/integration/test_admin_ui.py](/Users/ernest/GitHub/raja/tests/integration/test_admin_ui.py)

The API now reliably returns:

- `environment_id`
- `environment_url`

for each project block, even when those values are empty.

### 4. Post-apply discovery path

Implemented in [scripts/sagemaker_gaps.py](/Users/ernest/GitHub/raja/scripts/sagemaker_gaps.py):

- Detect whether the environment APIs are usable for the deployed domain
- Enumerate environments per project when supported
- Refresh `infra/tf-outputs.json` with discovered environment IDs
- Push discovered environment IDs into Lambda environment variables when present

This is useful scaffolding, but it is not the same as provisioning the environments.

---

## Missing

### 1. Custom blueprint registration

Not implemented:

- No `aws_datazone_environment_blueprint_configuration`
- No post-apply `put-environment-blueprint-configuration`

The live deploy shows `Custom blueprint: not found`, which means the assumption in
`05-blueprint.md` that `"CustomAWS"` is a built-in identifier is not valid in the current
deployed domain/account state, or is not available through the APIs currently exposed by this
domain version.

### 2. Environment profile creation

Not implemented:

- No `aws_datazone_environment_profile`
- No post-apply `create-environment-profile`

There is no `"RAJA registry"` profile today.

### 3. Environment creation

Not implemented:

- No `aws_datazone_environment` resources
- No post-apply `create-environment` calls
- No environment-role association via `associate-environment-role`

This is the main functional gap. The owner/users/guests projects still have no environments.

### 4. Environment IDs in Terraform outputs

Partially implemented, functionally unresolved:

- The output keys exist
- The deployed values are still empty strings

That means the blueprint requirement is not satisfied operationally.

### 5. Subscription targets

Not implemented.

The blueprint explicitly deferred this pending testing, and no follow-up implementation was added.

### 6. Verification target: DataZone file browser fixed

Not verified and currently very unlikely to be true because the environments do not exist.

Specifically, these blueprint acceptance criteria remain open:

- DataZone project overview file browser no longer errors
- Each project environment is `Active`
- Guests environment role write-deny is tested through a real environment path

---

## Root Cause

The biggest gap is not a missing local code change. It is a mismatch between the blueprint's
assumptions and the current AWS/DataZone reality for this stack.

Observed constraints during implementation:

1. The deployed domain behaves like a SageMaker Unified Studio / DataZone V2 domain, not the
   older environment-centric model assumed by the blueprint.
2. The current AWS provider version in use (`hashicorp/aws v6.36.0`) exposes some DataZone
   environment resources, but not enough to cleanly model the full custom-blueprint flow.
3. The underlying API shape is inconsistent with the Terraform schema:
   `associate-environment-role` exists as a separate API, while the Terraform environment resource
   does not expose that role attachment.
4. The live domain does not list a non-managed custom blueprint, so `"CustomAWS"` cannot simply be
   referenced as if it already exists.
5. `list-environment-profiles` also fails against this domain with `API not supported for domain version`,
   which is strong evidence that the blueprint is targeting an API surface not fully available here.

This aligns with the earlier warning in
[03-domain-tasks.md](/Users/ernest/GitHub/raja/specs/49-domain-ux/03-domain-tasks.md) under `G7`.

---

## What Was Proven

The following statement is now supported by the repo and by the live test run:

"RAJA can tolerate missing DataZone environments while still deploying, exposing admin structure,
and passing all current unit and integration tests."

The following statement is **not** yet supported:

"RAJA provisions the custom AWS service blueprint and creates active owner/users/guests DataZone
environments backed by `rajee_registry` and `rajee_test`."

---

## Recommended Next Steps

### Option A: Re-scope the blueprint to current V2 reality

Update [05-blueprint.md](/Users/ernest/GitHub/raja/specs/49-domain-ux/05-blueprint.md) to state:

- environment support is aspirational
- current stack only prepares IAM and control-plane plumbing
- actual environment creation is blocked by current DataZone domain/API behavior

This would make the spec match the deployed system.

### Option B: Build an explicit experimental provisioning path

If the goal is still to force the environment model through:

- identify the exact domain type and feature set required for custom AWS service blueprints
- confirm whether a different domain configuration is needed
- prototype the full sequence with AWS CLI/boto3 first:
  `put-environment-blueprint-configuration` → `create-environment-profile` →
  `create-environment` → `associate-environment-role`
- only after that works, encode it either in Terraform or in `scripts/sagemaker_gaps.py`

This is the highest-confidence path if the blueprint is still intended to become real.

### Option C: Drop environment-based UX as a dependency

If the project overview file browser is the only missing UX outcome, consider avoiding DataZone
environment/browser dependence entirely and surfacing registry browsing through RAJA/RALE instead.

That would align better with the current architecture, where access is already mediated through:

- DataZone listings for discovery
- RAJA/RALE/TAJ for delivery

---

## Status Call

Status: `partial`

Implemented enough:

- to preserve forward compatibility
- to expose environment metadata when it eventually exists
- to keep `./poe test-all` green

Not implemented enough:

- to claim that `specs/49-domain-ux/05-blueprint.md` is complete
- to claim that DataZone environments are provisioned or active
- to claim that the DataZone file browser issue is resolved
