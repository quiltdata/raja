# Setup Gaps: SageMaker/DataZone Integration

Gaps relative to the current codebase not covered in `01-sm-ticket.md`.

## AWS Account Prerequisites

- ✅ DataZone **domain** — provisioned by Terraform (`aws_datazone_domain.raja`).
- ✅ DataZone **project** per RAJ principal — `ensure_project_for_principal()` creates on demand; `datazone_project_id` stored in DynamoDB `principal_scopes` alongside existing `scopes`.
- ✅ IAM roles for Lambda functions — `rale_authorizer` and `control_plane` policies updated with `datazone:ListSubscriptionRequests`, `datazone:SearchListings`, `datazone:CreateSubscriptionRequest`, `datazone:AcceptSubscriptionRequest`, `datazone:CreateProject`, `datazone:ListProjects`.
- ✅ boto3-stubs — `boto3-stubs[datazone]` added to `pyproject.toml` dev extras.

## Terraform

- ✅ DataZone resources added to `infra/terraform/main.tf`:
  - `aws_iam_role.datazone_domain_execution` + policy attachment
  - `aws_datazone_domain.raja`
  - `aws_datazone_project.owner`
  - `aws_datazone_asset_type.quilt_package`
- ✅ `variables.tf` — `datazone_domain_name`, `datazone_owner_project_name`, `datazone_package_asset_type` added.
- ✅ `outputs.tf` — `datazone_domain_id`, `datazone_portal_url`, `datazone_owner_project_id`, `datazone_package_asset_type`, `datazone_package_asset_type_revision` added; consumed by integration tests via `tf-outputs.json`.
- ✅ Lambda env vars — `DATAZONE_DOMAIN_ID`, `DATAZONE_OWNER_PROJECT_ID`, `DATAZONE_PACKAGE_ASSET_TYPE`, `DATAZONE_PACKAGE_ASSET_TYPE_REVISION`, `PRINCIPAL_TABLE` added to both `rale_authorizer` and `control_plane` functions.
- AVP `aws_verifiedpermissions_policy_store` resource still present — not yet removed.

## Lambda Handlers

- ✅ `rale_authorizer/handler.py` — AVP `is_authorized()` replaced with `DataZoneService.has_package_grant()`; reads `datazone_project_id` from DynamoDB.

## Core Library

- ✅ `src/raja/datazone/` module created:
  - `DataZoneConfig` — reads domain/project/asset-type config from env.
  - `DataZoneService` — listing search, project creation, grant check, subscription orchestration.
  - `DataZonePackageListing` — value type for listing metadata.
  - `datazone_enabled()` — env-flag guard used by scripts and control plane.
  - `project_name_for_principal()` — deterministic slug for principal → project name.
- `src/raja/models.py` — no new Pydantic models added; DataZone state held in `DataZonePackageListing` dataclass and raw dicts from the API.

## Testing Infrastructure

- ✅ Unit tests updated — `test_control_plane_router.py`, `test_server_app.py`, `test_dependencies.py` all updated for DataZone-backed control plane (AVP mock replaced with DataZone mock).
- ✅ `tf-outputs.json` — new DataZone output keys (`datazone_domain_id`, `datazone_owner_project_id`, etc.) will be present after next `./poe deploy`.
- No dedicated unit test file for `src/raja/datazone/service.py` — moto does not support DataZone; manual stubs needed.
- Integration tests load DataZone outputs from `tf-outputs.json` — require a live deploy to validate end-to-end.
- No teardown logic for DataZone assets/subscriptions created during test runs — `ensure_*` methods are idempotent so re-runs are safe; cleanup is at project level only.

## CI/CD

- Integration workflow deploys Terraform and runs tests against live AWS.
- DataZone domain/project creation is slow and stateful; domain is provisioned once (not per test run).
- `skip_deletion_check = true` on domain and project resources prevents accidental teardown on `terraform destroy`.
