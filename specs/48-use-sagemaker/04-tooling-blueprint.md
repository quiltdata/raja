# Enable Tooling Blueprint for DataZone V2 Domain

## Goal

Fix the `VALIDATION_FAILED` / "Tooling environment not found" error on all three project
environments by enabling and configuring the **Tooling blueprint** at the domain level,
then re-triggering environment validation.

## Why

Custom environment blueprints in SageMaker Unified Studio (DataZone V2) depend on the
Tooling blueprint being configured first. It creates foundational per-project
infrastructure — IAM roles, SageMaker Lakehouse access, Athena query execution — that
custom blueprints layer on top of. Without it, every environment fails at validation.

This is why `raja-owner-env` (`b0ft0ic4t15q5j`) is `VALIDATION_FAILED` and the console
also surfaces the same error. The users/guests environments have the same problem (their
IDs are empty in `infra/tf-outputs.json`).

## Existing Resources

| Resource | Value |
| -------- | ----- |
| Domain | `dzd-45tgjtqytva0rr` |
| Provisioning role candidate | `arn:aws:iam::712023778557:role/raja-poc-datazone-domain-execution` |
| Manage access role candidate | `arn:aws:iam::712023778557:role/raja-poc-datazone-domain-service` |
| Region | `us-east-1` |
| Existing custom blueprint | discovered via `list-environment-blueprints --managed=false` |

## Tool

`infra/blueprints/raja-domain-blueprint.yaml` already exists for this exact purpose. It
deploys `AWS::DataZone::EnvironmentBlueprintConfiguration` to enable the `DefaultDataLake`
managed blueprint (Athena/SQL tooling) for the domain.

### Steps

1. **Find the actual blueprint ID** — the parameter default is `DefaultDataLake` but the
   comment says it varies by account. Get the real UUID:

   ```sh
   aws datazone list-environment-blueprints \
     --domain-identifier dzd-45tgjtqytva0rr \
     --managed --region us-east-1 \
     --query 'items[*].[id,name]' --output table
   ```

2. **Deploy the CloudFormation stack** (or add to Terraform — see below):

   ```sh
   aws cloudformation deploy \
     --template-file infra/blueprints/raja-domain-blueprint.yaml \
     --stack-name raja-domain-blueprint \
     --parameter-overrides \
       DomainId=dzd-45tgjtqytva0rr \
       AthenaBlueprintId=<actual-uuid-from-step-1> \
     --region us-east-1
   ```

3. **Re-trigger VALIDATION_FAILED environments** — after the blueprint is enabled,
   delete and recreate the three environments, or call `update-environment` to
   re-validate. The environment profile and custom blueprint ID are already known.

### Preferred: Add to Terraform

Rather than a standalone CloudFormation stack, add to `infra/terraform/main.tf`:

```hcl
resource "aws_datazone_environment_blueprint_configuration" "default_data_lake" {
  domain_id                = aws_datazone_domain.raja.id
  environment_blueprint_id = data.aws_datazone_environment_blueprint.default_data_lake.id
  enabled_regions          = [var.aws_region]
}

data "aws_datazone_environment_blueprint" "default_data_lake" {
  domain_id = aws_datazone_domain.raja.id
  name      = "DefaultDataLake"
  managed   = true
}
```

This is cleaner than a separate CloudFormation stack and keeps everything in Terraform.

### Re-triggering Environments

After blueprint is enabled, existing `VALIDATION_FAILED` environments must be recycled.
Add `_ensure_environments` to `sagemaker_gaps.py` that:

- Lists environments per project
- For any in `VALIDATION_FAILED`, deletes and re-creates using the same profile/blueprint

## Interface

```sh
# After Terraform apply or CloudFormation deploy:
uv run python scripts/sagemaker_gaps.py
```

## Prerequisites

- `infra/tf-outputs.json` must be present with domain ID and project IDs
- Caller must have `cloudformation:*` or `datazone:PutEnvironmentBlueprintConfiguration`
- The `DefaultDataLake` managed blueprint must be available in the domain

## Tasks

- [ ] Add `_ensure_tooling_blueprint` to `scripts/sagemaker_gaps.py`
- [ ] Add `_ensure_environments` (create/re-trigger) to `scripts/sagemaker_gaps.py`
- [ ] Run `sagemaker_gaps.py` and confirm all three environments reach `ACTIVE`
- [ ] Re-run `import_glue_db.py` to re-grant Alpha's LF access (revoked in previous step)
- [ ] Create DataZone Glue data source in Alpha project pointing at
      `icebergdatabase-v9cxuqnwjj5a`
- [ ] Run crawl; verify 4 Iceberg tables appear as DataZone assets in Alpha
- [ ] Subscribe Bio and Compute projects to the Iceberg assets and confirm DataZone
      provisions their LF access automatically

## Notes

- VPC config is optional for Tooling blueprint basic enablement; add it only if
  provisioning fails without it (the existing Terraform VPC can be used)
- `put-environment-blueprint-configuration` is idempotent — safe to re-run
- The Tooling blueprint creates one tooling environment per project automatically once
  configured; these are internal and not the custom `raja-*-env` environments
