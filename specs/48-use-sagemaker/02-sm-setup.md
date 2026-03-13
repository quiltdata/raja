# Setup Gaps: SageMaker/DataZone Integration

Gaps relative to the current codebase not covered in `01-sm-ticket.md`.

## AWS Account Prerequisites

- A DataZone **domain** must exist (or be provisioned) — no current automation for this.
- A DataZone **project** per RAJ principal must exist — currently only DynamoDB `principal_scopes` tracks principals.
- IAM roles for Lambda functions need new permissions: `datazone:GetAsset`, `datazone:ListSubscriptionGrants`, `datazone:CreateSubscriptionRequest`, `datazone:AcceptSubscription`, etc.
- Boto3 stubs (`boto3-stubs`) do not currently include DataZone — type-checking support must be added.

## Terraform

- No DataZone resources in `infra/terraform/main.tf` — domain, project, asset type, and grant resources all need new blocks.
- `variables.tf` has no DataZone variables (`datazone_domain_id`, project mappings, asset type name).
- `tf-outputs.json` (consumed by integration tests) has no DataZone output values — tests will break without new outputs.
- AVP schema and policy loading scripts (`apply_avp_schema.py`, `load_policies.py`) have no DataZone equivalent.

## Lambda Handlers

- `rale_authorizer/handler.py` calls `verifiedpermissions:is_authorized()` — no DataZone client or grant-check logic exists anywhere in the codebase.
- Environment variable `POLICY_STORE_ID` is AVP-specific — DataZone equivalents (`DATAZONE_DOMAIN_ID`, `DATAZONE_PROJECT_ID`) are not defined in any Terraform or handler config.

## Core Library

- No `src/raja/datazone/` module — grant lookup, asset resolution, and subscription orchestration need a new module (parallel to `src/raja/cedar/`).
- `src/raja/models.py` has no DataZone-specific models (asset, grant, subscription).

## Testing Infrastructure

- Integration tests load fixtures from `tf-outputs.json` — no DataZone keys exist there yet.
- No mocking layer for DataZone API calls — unit tests for any new DataZone module will need moto or manual stubs (moto does not currently support DataZone).
- `tests/integration/test_rale_end_to_end.py` exercises the full AVP path — needs a DataZone-backed parallel or replacement.
- No seed script for DataZone test state (equivalent to `scripts/seed_test_data.py` for AVP/DynamoDB).

## CI/CD

- Integration workflow deploys Terraform and runs tests against live AWS — DataZone domain/project creation is slow and stateful; **domain is provisioned once by Terraform** (not per test run).
- Projects map to RAJ principals (not environments); teardown only needs to clean up assets and subscriptions — not projects or the domain.
- No teardown logic for DataZone assets/subscriptions created during test runs — integration tests must be idempotent and clean up at the project level.
