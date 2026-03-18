# Tooling Environment — What Works, What Doesn't, Hypotheses

## Context

Creating a **Tooling environment** per project is required before the custom
`raja-poc` blueprint environments can deploy. Without it every custom environment
fails immediately with:

```
VALIDATION_FAILED: "Tooling environment not found for project: <project-id>"
```

We've been iterating to get the Tooling environment (`cjegf7f6kky6w7`) to reach
`ACTIVE`. It keeps failing at the DataZone pre-validation stage (before
CloudFormation even runs) with:

```
VALIDATION_FAILED: "Invalid S3 path provided null"
```

---

## What We Did (in order)

### 1. Enabled `DefaultDataLake` blueprint — ✅ Necessary but not sufficient

```sh
# Added to infra/terraform/main.tf:
resource "aws_datazone_environment_blueprint_configuration" "default_data_lake" {
  domain_id                = aws_datazone_domain.raja.id
  environment_blueprint_id = "d6y5smpdi8x9lz"   # DataLake
  enabled_regions          = [var.aws_region]
}
```

Applied via `./poe deploy`. Didn't fix custom env VALIDATION_FAILED.

### 2. Configured Tooling blueprint (`cjegf7f6kky6w7`) — ✅ Required

```sh
aws datazone put-environment-blueprint-configuration \
  --domain-identifier dzd-45tgjtqytva0rr \
  --environment-blueprint-id cjegf7f6kky6w7 \
  --provisioning-role-arn arn:aws:iam::712023778557:role/raja-standalone-datazone-domain-execution \
  --manage-access-role-arn arn:aws:iam::712023778557:role/raja-standalone-datazone-domain-execution \
  --enabled-regions us-east-1 \
  --regional-parameters '{"us-east-1":{...}}' \
  --region us-east-1
```

Blocked `create-environment` with AccessDeniedException until we also:

### 3. Added `CREATE_ENVIRONMENT_FROM_BLUEPRINT` grants — ✅ Required

```sh
# For both OWNER and CONTRIBUTOR on both blueprints:
aws datazone add-policy-grant \
  --entity-identifier "712023778557:cjegf7f6kky6w7" \
  --entity-type ENVIRONMENT_BLUEPRINT_CONFIGURATION \
  --policy-type CREATE_ENVIRONMENT_FROM_BLUEPRINT \
  --principal '{"project":{"projectDesignation":"OWNER",...}}'
```

After this `create-environment` succeeds (returns CREATING).

### 4. Added `raja-tooling` env config to project profile — ✅ Required

Updated `raja-default-profile` (`dsnx4ajzbyro2v`) via `update-project-profile` to
include the Tooling blueprint alongside the custom blueprint. This is needed to get a
valid `environmentConfigurationId` — `create-environment` with
`--environment-blueprint-id` alone returns AccessDeniedException even after step 3;
only `--environment-configuration-id` works in V2 domains.

### 5. Added VPC regional parameters — ✅ Changed error (progress)

```json
{"us-east-1": {"vpcId": "vpc-00c22ea37a6b788f6", "subnetIds": "subnet-079a6df47d554efa1,subnet-0c58800ce7e36dc40"}}
```

Changed error from "needs to enable atleast one region" → "Invalid S3 path provided null".

### 6. Added `s3Location` regional parameter — ❌ No effect

```json
{"us-east-1": {"...", "s3Location": "s3://raja-poc-registry-.../sagemaker-tooling/"}}
```

Same error. Wrong key.

### 7. Added `s3BucketArn` regional parameter — ❌ Still failing

```json
{"us-east-1": {"...", "s3BucketArn": "arn:aws:s3:::raja-poc-registry-712023778557-us-east-1"}}
```

Same error. The CloudFormation template has `s3BucketArn` as an injected parameter,
but setting it in `regionalParameters` doesn't seem to reach the validation check.

### 8. Added `s3BucketPrefix` to project profile parameter overrides — ❌ Still failing

```json
{"parameterOverrides": [{"name": "s3BucketPrefix", "value": "raja-tooling", "isEditable": false}]}
```

Tells the Tooling blueprint to create a bucket named `raja-tooling-{projectId}` instead
of using the domain default. Still same error. Either not reaching validation, or
the check happens before the per-project bucket would be created.

---

## Root Cause Hypothesis

The DataZone V2 domain was created **without an S3 storage location**:

```json
"singleSignOn": {"type": "DISABLED"}  // domain has no default S3 bucket
```

The Tooling blueprint pre-validation reads the **domain-level S3 bucket** (not the
per-project one from `s3BucketPrefix`) and fails if it is null.

Evidence:
- The error is a DataZone pre-validation failure (no CloudFormation stack created)
- `s3BucketArn` in `regionalParameters` should fix it but doesn't — DataZone may
  be reading from the domain object directly, bypassing `regionalParameters`
- `s3BucketPrefix` creates a project bucket at deployment time, too late for the
  pre-validation check
- `update-domain` has no S3 option → the domain S3 config is immutable post-creation

## Most Likely Fix

The domain needs to be recreated with S3 enabled. In Terraform, `aws_datazone_domain`
needs to include the S3 storage location at creation time. From the AWS provider
resource docs, this may require adding a `kms_key_identifier` and ensuring SageMaker
can access a dedicated S3 bucket.

**Alternative path:** Check whether there is an AWS-managed S3 bucket for this
SageMaker Unified Studio domain that already exists (SageMaker may have created one
automatically) and reference that in the `regionalParameters`.

```sh
# Check for auto-created SageMaker domain bucket:
aws s3 ls | grep sagemaker | grep 712023778557
aws s3 ls | grep dzd-45tgjtqytva0rr
```

## Other Hypotheses (lower probability)

- The `availabilityZones` regional parameter is also required alongside `subnetIds`
  but it's absent (the CF template needs it, and DataZone might pre-validate it)
- The `manageAccessRoleArn` must be a different role from `provisioningRoleArn`;
  both currently point to `raja-standalone-datazone-domain-execution`
- The Tooling blueprint configuration needs `regionalParameters` key to be the
  region name as `us-east-1` but DataZone is matching it differently internally

## Next Steps

1. **Check for an existing SageMaker S3 bucket** (auto-created by the domain):
   ```sh
   aws s3 ls | grep -E "sagemaker|dzd-45"
   ```

2. **If no bucket exists** — either:
   a. Add dedicated S3 bucket to Terraform and recreate domain with S3 enabled, OR
   b. Create a bucket manually and add its ARN as `s3BucketArn` regional parameter

3. **Try `availabilityZones` in regional parameters**:
   ```json
   {"us-east-1": {"vpcId": "...", "subnetIds": "...", "availabilityZones": "us-east-1a,us-east-1b", "s3BucketArn": "..."}}
   ```

4. If all else fails — **try creating the Tooling environment via the SageMaker
   Unified Studio console** while logged in as a project owner. The console may
   populate the S3 path from a different source than the CLI path.

## Key IDs (current state)

| Resource | Value |
|----------|-------|
| Tooling blueprint | `cjegf7f6kky6w7` |
| Tooling env config ID (current) | `a402c5c4-da7e-4abc-ae7c-da72629f3ef7` |
| Registry env config ID (current) | `ead6b6f5-af89-49d9-bbf1-56d1bf56ef45` |
| VPC | `vpc-00c22ea37a6b788f6` |
| Private subnets | `subnet-079a6df47d554efa1`, `subnet-0c58800ce7e36dc40` |
| s3BucketArn tried | `arn:aws:s3:::raja-poc-registry-712023778557-us-east-1` |

> **Note:** `environmentConfigurationId` changes every time `update-project-profile`
> is called. Always use `get-project-profile` to get the current IDs before
> creating environments.
