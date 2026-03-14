# Issue #48: Use SageMaker

## Overview

Migrate RAJA's authorization backend from Amazon Verified Permissions (AVP) to SageMaker/DataZone as the source of truth for project-to-package grants.

## Tasks

### ✅ Define custom asset type

- Created DataZone custom asset type (`QuiltPackage`) via `aws_datazone_asset_type` in Terraform.

### ✅ Create package assets

- `DataZoneService.ensure_package_listing()` creates an asset per Quilt package with:
  - `externalIdentifier`: the `quilt+s3://…` URI
  - `name`: the package name
  - asset type `QuiltPackage`
- Asset is published as a DataZone listing after creation.

### ✅ Map principals

- `DataZoneService.ensure_project_for_principal()` creates one DataZone project per RAJ principal (name derived deterministically via `project_name_for_principal()`).
- `seed_test_data.py` calls this during bootstrapping and stores `datazone_project_id` alongside `scopes` in the DynamoDB `principal_scopes` table.
- The `POST /principals` control-plane endpoint also calls this when DataZone is enabled.
- AVP-backed policy authoring (`POST /policies`, `PUT /policies/{id}`) now returns HTTP 410 Gone — explicitly decommissioned with no substitute for the POC.

### ✅ Adopt DataZone grants as source of truth

- DataZone subscription/grant state (ACCEPTED subscription requests) represents `project → package` access.
- `DataZoneService.has_package_grant()` checks for an ACCEPTED subscription for the given project + listing.

### ✅ Implement grant orchestration

- `DataZoneService.ensure_project_package_grant(project_id, quilt_uri)`:
  1. Calls `ensure_package_listing()` to find or create the asset listing.
  2. Checks for an existing ACCEPTED subscription (idempotent).
  3. Creates a PENDING subscription request if none exists.
  4. Immediately calls `accept_subscription_request()` to approve it.

### ✅ Automate control-plane workflow

- `seed_packages.py` calls `ensure_project_package_grant()` during package seeding when `DATAZONE_DOMAIN_ID` is set.
- `seed_test_data.py` calls `ensure_project_for_principal()` for each test principal.

### ✅ Update RAJ minting logic

- **Semantic simplification applied:** Binary `project → package` grant replaces AVP's full `(principal, action, resource, context)` tuple.
- `rale_authorizer/handler.py` now:
  1. Reads `datazone_project_id` from DynamoDB `principal_scopes` table.
  2. Calls `DataZoneService.has_package_grant()` instead of `verifiedpermissions:is_authorized()`.
- `control_plane.py` `/token/package` and `/token/translation` endpoints use `_authorize_package_with_datazone()`.

### ✅ Integrate grant updates

- `POST /principals` in the control plane calls `ensure_project_for_principal()` when `DATAZONE_DOMAIN_ID` is set, keeping DynamoDB and DataZone in sync on every principal write.

### Backfill existing permissions

- Not yet automated for production; test backfill handled by `seed_test_data.py` + `seed_packages.py`.
- A full migration script for existing AVP grants is deferred post-POC.

### ✅ Remove AVP dependency (partial)

- AVP `is_authorized()` calls removed from authorizer Lambda and control-plane token endpoints.
- `POLICY_STORE_ID` env var retained in control-plane Lambda for token audit logging (`_policy_plane_id()` returns `datazone:<domain_id>` when DataZone is enabled).
- `aws_verifiedpermissions_policy_store` Terraform resource kept — not yet removed.

### Optional safety phase

- Dual evaluation not implemented; the POC cuts over directly.
