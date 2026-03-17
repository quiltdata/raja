# RAJA Registry Blueprint

Custom DataZone / SageMaker Unified Studio blueprint that gives each RAJA project
environment access to the existing `rajee_registry` S3 bucket without provisioning
new storage.

---

## Why this exists

DataZone V2 domains (SageMaker Unified Studio) do not support:

- `CreateEnvironmentProfile` — API not supported for domain version
- The `CustomAWS` built-in blueprint identifier assumed in earlier specs

Custom blueprints must be created via the console (no `CreateEnvironmentBlueprint` API
exists). Once created, the blueprint ID is stable and all subsequent steps — enabling
the blueprint on the domain, creating per-project environments, associating tier roles —
are handled by Terraform.

---

## One-time console setup

### Step 1 — Open the Blueprints page

SageMaker Unified Studio console → domain `raja-poc` → left nav → **Blueprints** →
**Create blueprint**

### Step 2 — Upload template

| Field | Value |
| --- | --- |
| Template source | Upload file |
| File | `infra/blueprints/raja-registry-blueprint.yaml` |

### Step 3 — Set blueprint name and description

| Field | Value |
| --- | --- |
| Name | `raja-poc` |
| Description | `SageMaker Blueprint for RALE Stack` |

### Step 4 — Review parameters

The console will warn that the following parameter names are **reserved words** and
will not be prompted to users. This is expected — DataZone injects them automatically:

- `datazoneEnvironmentEnvironmentId`
- `datazoneEnvironmentProjectId`
- `userRoleArn`

The user-supplied parameters that will be shown at environment creation time:

| Parameter | Value to supply |
| --- | --- |
| `RegistryBucketName` | `raja-poc-registry-712023778557-us-east-1` |
| `TestBucketName` | `raja-poc-test-712023778557-us-east-1` |

### Step 5 — Enable blueprint

Check **Enable blueprint** (enabled by default on this screen).

| Field | Value |
| --- | --- |
| Provisioning role | `raja-standalone-datazone-domain-execution` |
| | `arn:aws:iam::712023778557:role/raja-standalone-datazone-domain-execution` |
| Domain unit | `raja-poc` (Root domain) — already present, leave Cascade On |

> The provisioning role is used by SageMaker Unified Studio to run the CloudFormation
> template (create IAM managed policies, SSM parameters) when an environment is
> provisioned. The domain execution role has the necessary permissions.

### Step 6 — Create and note the blueprint ID

Click **Create**. The console will show the new blueprint with an ID of the form
`dzbt-xxxxxxxxxxxx`. **Record this ID** — it is needed in the Terraform step below.

---

## Terraform setup (already applied)

The blueprint ID is hardcoded in `infra/terraform/main.tf`:

```hcl
locals {
  raja_blueprint_id = "4b1p5czd9uf9uv"
}

resource "aws_datazone_environment_blueprint_configuration" "raja_registry" {
  domain_id                = aws_datazone_domain.raja.id
  environment_blueprint_id = local.raja_blueprint_id
  enabled_regions          = [var.aws_region]
  provisioning_role_arn    = aws_iam_role.datazone_domain_execution.arn
  manage_access_role_arn   = aws_iam_role.datazone_domain_execution.arn
}
```

> **Note:** The `aws_datazone_environment` Terraform resource requires
> `profile_identifier`, which is not supported on V2 domains. Environment creation
> is handled post-apply by `scripts/sagemaker_gaps.py` using the DataZone API
> directly (`CreateEnvironment` + `AssociateEnvironmentRole`).

## Environment provisioning (post-apply)

After `terraform apply`, run:

```bash
python scripts/sagemaker_gaps.py
```

The script will:

1. Confirm the blueprint configuration is enabled
2. Create `raja-owner-env`, `raja-users-env`, `raja-guests-env` via `CreateEnvironment`
3. Associate the tier-specific IAM roles via `AssociateEnvironmentRole`
4. Write the resulting environment IDs back into `infra/tf-outputs.json` and Lambda config

---

## Current live values

| Resource | Value |
| --- | --- |
| Domain ID | `dzd-45tgjtqytva0rr` |
| Domain portal | `https://dzd-45tgjtqytva0rr.sagemaker.us-east-1.on.aws` |
| Registry bucket | `raja-poc-registry-712023778557-us-east-1` |
| Test bucket | `raja-poc-test-712023778557-us-east-1` |
| Owner project | `3oyby5rkdifoo7` |
| Users project | `bbkzgt9ejr878n` |
| Guests project | `61sqtlj3m1ma7b` |
| Owner env role | `arn:aws:iam::712023778557:role/raja-dz-env-owner` |
| Users env role | `arn:aws:iam::712023778557:role/raja-dz-env-users` |
| Guests env role | `arn:aws:iam::712023778557:role/raja-dz-env-guests` |
| Provisioning role | `arn:aws:iam::712023778557:role/raja-standalone-datazone-domain-execution` |
| Blueprint ID | `4b1p5czd9uf9uv` |
