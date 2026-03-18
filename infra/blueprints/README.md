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

---

## Lessons Learned (painful)

### 1. `CREATE_ENVIRONMENT_FROM_BLUEPRINT` grants are not inherited from IAM

Even with `AdministratorAccess`, `create-environment` returns `AccessDeniedException`
until you explicitly add DataZone policy grants for **both** `OWNER` and `CONTRIBUTOR`
project designations on the `ENVIRONMENT_BLUEPRINT_CONFIGURATION` entity:

```sh
for designation in OWNER CONTRIBUTOR; do
  aws datazone add-policy-grant \
    --domain-identifier dzd-45tgjtqytva0rr \
    --entity-identifier "712023778557:${BLUEPRINT_ID}" \
    --entity-type ENVIRONMENT_BLUEPRINT_CONFIGURATION \
    --policy-type CREATE_ENVIRONMENT_FROM_BLUEPRINT \
    --principal "{\"project\":{\"projectDesignation\":\"${designation}\",\"projectGrantFilter\":{\"domainUnitFilter\":{\"domainUnit\":\"3iisyygqr938dj\",\"includeChildDomainUnits\":true}}}}" \
    --region us-east-1
done
```

Required for **every** blueprint (custom and managed Tooling).

### 2. V2 domains: use `--environment-configuration-id`, not `--environment-blueprint-id`

In DataZone V2, `create-environment --environment-blueprint-id` returns
`AccessDeniedException`. You must use `--environment-configuration-id`, obtained from
the project profile via:

```sh
aws datazone get-project-profile \
  --domain-identifier dzd-45tgjtqytva0rr \
  --identifier <profile-id> \
  --region us-east-1 \
  --query 'environmentConfigurations[*].[id,environmentBlueprintId,name]'
```

The config ID **rotates on every `update-project-profile` call** — always re-fetch it.

### 3. Tooling blueprint is a required prerequisite for all custom blueprints

Every custom environment fails immediately with
`VALIDATION_FAILED: "Tooling environment not found for project: <id>"` unless the
**Tooling blueprint** (`cjegf7f6kky6w7`) is configured at the domain level first.
It creates per-project SageMaker infrastructure (IAM roles, Athena workgroup, etc.)
that custom blueprints layer on top of.

Configure it with:

```sh
aws datazone put-environment-blueprint-configuration \
  --domain-identifier dzd-45tgjtqytva0rr \
  --environment-blueprint-id cjegf7f6kky6w7 \
  --provisioning-role-arn arn:aws:iam::712023778557:role/raja-standalone-datazone-domain-execution \
  --manage-access-role-arn arn:aws:iam::712023778557:role/raja-standalone-datazone-domain-execution \
  --enabled-regions us-east-1 \
  --regional-parameters '{"us-east-1":{"vpcId":"vpc-00c22ea37a6b788f6","subnetIds":"subnet-079a6df47d554efa1,subnet-0c58800ce7e36dc40"}}' \
  --region us-east-1
```

VPC + subnet IDs are required. Without them you get
`"needs to enable atleast one region"`.

### 4. Tooling blueprint requires a domain-level S3 bucket — and it's immutable

The Tooling blueprint pre-validation reads a **domain-level S3 bucket** before
CloudFormation even runs. If the domain was created with `s3Location: {type: "DISABLED"}`
(which is what happens when you don't specify an S3 bucket at domain creation time),
every Tooling environment creation fails with:

```text
VALIDATION_FAILED: "Invalid S3 path provided null"
```

Setting `s3BucketArn` in `regionalParameters` does **not** fix this — DataZone reads
the S3 location from the domain object directly, bypassing `regionalParameters`.
`update-domain` has no S3 option. The S3 config is **immutable post-creation**.

The fix is to recreate the domain with an S3 bucket specified, or find the
auto-created SageMaker domain bucket (if one was created by SageMaker Unified Studio):

```sh
aws s3 ls | grep -E "sagemaker|dzd-45"
```

### 5. IAM users can be DataZone project members

DataZone V2 supports IAM users (not just SSO users) as project members when the domain
has SSO disabled (`"singleSignOn": {"type": "DISABLED"}`). The console shows them and
you can assign Owner/Contributor roles to IAM users directly.

### 6. Tooling blueprint `regionalParameters` keys

The only keys that matter for the Tooling blueprint:

| Key | Example |
| --- | --- |
| `vpcId` | `vpc-00c22ea37a6b788f6` |
| `subnetIds` | `subnet-079a6df47d554efa1,subnet-0c58800ce7e36dc40` (comma-separated string) |
| `s3BucketArn` | *(doesn't work — see lesson 4)* |
| `s3Location` | *(wrong key — no effect)* |
| `s3BucketPrefix` | *(per-project bucket prefix — too late for pre-validation)* |
