# 08 Environment Config: What Almost Worked

## Summary

This doc records a live debugging session against domain `dzd-45tgjtqytva0rr` that got
environments to the point of CREATING before hitting a hard service-level blocker. Every error
below was solved. The final one was not.

---

## Starting state

`sagemaker_gaps.py` failed at `CreateEnvironment` with:

```
AccessDeniedException: User is not permitted to perform operation: CreateEnvironment
```

Previous analysis (specs 06 and 07) concluded this was either a V2 API limitation or an
unresolvable DataZone policy grant problem. Both conclusions were wrong.

---

## Root cause of "User is not permitted"

The real cause was **not** a missing IAM or DataZone policy grant. It was a missing
`environmentConfigurations` entry on the project profile.

In DataZone V2, a project profile must explicitly declare which blueprints are available for
environment creation via `environmentConfigurations`. Without this, `CreateEnvironment` returns
the misleading "User is not permitted" error for any caller, regardless of IAM or DataZone
grants.

The fix is `update_project_profile` with an `environmentConfigurations` list — an API that
`sagemaker_gaps.py` was never calling.

### Key API

```python
client.update_project_profile(
    domainIdentifier=domain_id,
    identifier=profile_id,          # 'dsnx4ajzbyro2v'
    environmentConfigurations=[
        {
            'name': 'raja-registry',
            'environmentBlueprintId': '4b1p5czd9uf9uv',
            'deploymentMode': 'ON_DEMAND',
            'awsAccount': {'awsAccountId': account_id},
            'awsRegion': {'regionName': region},
            'configurationParameters': {
                'parameterOverrides': [
                    {'name': 'RegistryBucketName', 'value': registry_bucket, 'isEditable': False},
                    {'name': 'TestBucketName',     'value': test_bucket,     'isEditable': False},
                ]
            },
        }
    ],
)
```

This returns an `id` for the configuration entry (e.g. `bff53d53-1b4b-4229-9f9e-8c63e7228741`),
which is the `environmentConfigurationId` required by `CreateEnvironment`.

---

## Error sequence and fixes

### Error 1 — "User is not permitted to perform operation: CreateEnvironment"

**Cause:** No `environmentConfigurations` on the project profile `raja-default-profile`.

**Fix:** Call `update_project_profile` as shown above.

---

### Error 2 — "Only ON_DEMAND environment configuration can be used"

```
ValidationException: User is not permitted to perform operation Create Environment with
ON_CREATE environment configuration ... Only ON_DEMAND environment configuration can be
used to perform operation Create Environment
```

**Cause:** `deploymentMode` was set to `ON_CREATE`, which is for automatic deployment on
project creation. Manual `CreateEnvironment` calls require `ON_DEMAND`.

**Fix:** Set `deploymentMode: 'ON_DEMAND'` in the `environmentConfigurations` entry.

---

### Error 3 — "Project cannot override existing value of parameters"

```
ValidationException: Project cannot override existing value of parameters
'RegistryBucketName, TestBucketName' which are marked as non-editable in the project profile.
```

**Cause:** The blueprint YAML marks `RegistryBucketName` and `TestBucketName` as
`isEditable: false`. When those fields are non-editable at the blueprint level, the
`userParameters` in `CreateEnvironment` are rejected — the values must come from the profile's
`configurationParameters.parameterOverrides` instead.

**Fix:** Move the parameter values from `CreateEnvironment`'s `userParameters` into
`configurationParameters.parameterOverrides` in the profile's `environmentConfigurations`
entry. `CreateEnvironment` is then called without `userParameters`.

---

### Error 4 — "Access denied to blueprint template"

```
VALIDATION_FAILED: Access denied to blueprint template for blueprintId: 4b1p5czd9uf9uv
```

**Cause:** The domain execution role `raja-standalone-datazone-domain-execution` lacked S3
read access to the bucket where the SageMaker console uploaded the CloudFormation template:

```
s3://amazon-sagemaker-cf-templates-us-east-1-14b75aecf022/
    2026-03-16T230824.306Z8k0-raja-registry-blueprint.yaml
```

The `SageMakerStudioDomainExecutionRolePolicy` managed policy does not include access to
this bucket.

**Fix:** Add an inline policy to the execution role:

```json
{
  "Effect": "Allow",
  "Action": ["s3:GetObject", "s3:GetObjectVersion"],
  "Resource": "arn:aws:s3:::amazon-sagemaker-cf-templates-us-east-1-*/*"
}
```

This is now in `infra/terraform/main.tf` as
`aws_iam_role_policy.datazone_domain_execution_blueprint`.

---

### Error 5 — "cloudformation:DescribeStacks not authorized"

```
FAILED: User ... is not authorized to perform: cloudformation:DescribeStacks on resource:
arn:aws:cloudformation:us-east-1:712023778557:stack/DataZone-Env-{env_id}/*
```

**Cause:** The execution role had no CloudFormation permissions. DataZone deploys the blueprint
as a CloudFormation stack named `DataZone-Env-{environment_id}`.

**Fix:** Add CloudFormation stack permissions scoped to `DataZone-Env-*`:

```json
{
  "Effect": "Allow",
  "Action": [
    "cloudformation:CreateStack", "cloudformation:UpdateStack", "cloudformation:DeleteStack",
    "cloudformation:DescribeStacks", "cloudformation:DescribeStackEvents",
    "cloudformation:DescribeStackResource", "cloudformation:DescribeStackResources",
    "cloudformation:GetTemplate"
  ],
  "Resource": "arn:aws:cloudformation:us-east-1:{account}:stack/DataZone-Env-*"
}
```

Also add `cloudformation:ValidateTemplate` with `Resource: "*"` (this action does not accept
a stack ARN as resource):

```json
{
  "Effect": "Allow",
  "Action": ["cloudformation:ValidateTemplate"],
  "Resource": "*"
}
```

Both are in the same `aws_iam_role_policy.datazone_domain_execution_blueprint` Terraform
resource.

---

### Error 6 — "cloudformation:ValidateTemplate not authorized" (after Error 5 fix)

Same root cause as Error 5 — the initial fix scoped `ValidateTemplate` to the stack ARN,
which CloudFormation rejects. Fixed by splitting into two statements as shown above.

---

### Error 7 — "Tooling environment not found for project" (HARD BLOCKER)

```
VALIDATION_FAILED (code 400): Tooling environment not found for project: {project_id}
```

**Cause:** DataZone V2 requires a **Tooling environment** to exist in a project before any
custom blueprint environment can be created. The Tooling blueprint (ID `cjegf7f6kky6w7`) was
visible in the AWS Console as "Disabled" for domain `raja-poc`.

The Tooling blueprint provisions SageMaker Studio domains, IAM roles, and security groups —
substantial managed infrastructure per project. RAJA does not use SageMaker Studio and does not
want this infrastructure.

**This is a hard service-side constraint.** All three projects (owner, users, guests) hit this
error identically. There is no API parameter or policy grant that bypasses it.

---

## What was deployed as a side effect

During this session, the following changes were made that persist in the live stack:

1. **`aws_iam_role_policy.datazone_domain_execution_blueprint`** added to Terraform
   (in `infra/terraform/main.tf`) — grants the execution role S3 + CloudFormation + IAM
   permissions needed for blueprint provisioning. This is correct infrastructure regardless
   of whether environments are ever created.

2. **`environmentConfigurations` on `raja-default-profile`** — the project profile now has
   blueprint deployment settings for `raja-poc` in `ON_DEMAND` mode with `RegistryBucketName`
   and `TestBucketName` pre-set. Configuration ID: `bff53d53-1b4b-4229-9f9e-8c63e7228741`.
   This is useful if the Tooling blocker is ever resolved.

3. **`DataLake` blueprint configured on domain** — an unintended side effect of testing managed
   blueprints. The `DataLake` blueprint (ID `d6y5smpdi8x9lz`) was enabled via
   `put_environment_blueprint_configuration` with the domain execution role. This is inert but
   present.

---

## Path to unblocking

The Tooling environment requirement has two known paths:

### Option A: Enable Tooling blueprint

Enable `cjegf7f6kky6w7` (Tooling) on the domain and create Tooling environments for each
project. This provisions SageMaker Studio infrastructure (IAM roles, security groups, possibly
a VPC). Expensive and not aligned with RAJA's architecture.

### Option B: Remove environment dependency (recommended)

Implement spec `07-environment-fix.md` Option B: remove `DATAZONE_*_ENVIRONMENT_ID` variables,
stop the environment provisioning path, add `registry_url` to the admin structure.

---

## Status

`08-environment-config.md` status: **blocker documented, ready for Option B**

The custom blueprint provisioning chain is now fully understood. Every permission gap has been
identified and fixed. The single remaining obstacle is a service-side prerequisite
(Tooling environment) that RAJA cannot and should not satisfy.
