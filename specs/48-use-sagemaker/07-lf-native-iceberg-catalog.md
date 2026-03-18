# LF-Native Iceberg Catalog for Subscription-Gated Quilt Sessions

## Goal

Create a new Lake Formation-native Glue database that mirrors the four Quilt Iceberg
tables from the existing IAM-managed catalog. DataZone subscription approval will
automatically provision Lake Formation `SELECT + DESCRIBE` grants to the subscribing
project's environment role — no manual scripts required.

## Why the Existing Catalog Does Not Work

`icebergdatabase-v9cxuqnwjj5a` uses `IAM_ALLOWED_PRINCIPALS` (hybrid/IAM-managed mode).
DataZone can only auto-provision Lake Formation grants for **LF-native** databases.
A new database is created instead of migrating the existing one to avoid breaking
existing consumers.

## Key Limitation (POC Scope)

Iceberg tables track their current state via a `metadata_location` pointer in the
Glue catalog. Terraform copies this value from the source tables **at apply time**.
After any Quilt write to the Iceberg tables, the new database's tables will have a
stale `metadata_location` until the next `terraform apply`.

**This is acceptable for the POC** — the goal is to prove the
subscription → LF grant → environment role access chain works, not to run production
workloads against the new catalog.

**For production:** Quilt must be configured to write to the new LF-native database
instead of the old one. That is a Quilt configuration change, out of scope here.

## New Terraform Variable

```hcl
# variables.tf
variable "iceberg_s3_bucket" {
  description = "S3 bucket containing the Quilt Iceberg tables (without s3:// prefix)."
  type        = string
  default     = ""
}
```

All other inputs (domain ID, project IDs, table names) are already in Terraform state
or hardcoded as locals.

## Terraform Changes (`infra/terraform/main.tf`)

### 1. Locals

```hcl
locals {
  iceberg_enabled             = var.iceberg_s3_bucket != ""
  iceberg_source_database     = "icebergdatabase-v9cxuqnwjj5a"
  iceberg_native_database     = "${var.stack_name}-iceberg-lf"
  iceberg_table_names         = ["package_entry", "package_manifest", "package_revision", "package_tag"]
}
```

### 2. Read existing table definitions

```hcl
data "aws_glue_catalog_table" "iceberg_source" {
  for_each      = local.iceberg_enabled ? toset(local.iceberg_table_names) : toset([])
  database_name = local.iceberg_source_database
  name          = each.key
}
```

### 3. New LF-native Glue database

```hcl
resource "aws_glue_catalog_database" "iceberg_lf" {
  count = local.iceberg_enabled ? 1 : 0
  name  = local.iceberg_native_database
}
```

No `create_table_default_permission` block → LF is the authority (not IAM).

### 4. Mirror Glue tables

```hcl
resource "aws_glue_catalog_table" "iceberg_lf" {
  for_each      = local.iceberg_enabled ? toset(local.iceberg_table_names) : toset([])
  database_name = aws_glue_catalog_database.iceberg_lf[0].name
  name          = each.key

  table_type = "EXTERNAL_TABLE"

  parameters = merge(
    data.aws_glue_catalog_table.iceberg_source[each.key].parameters,
    { "table_type" = "ICEBERG" }
  )

  storage_descriptor {
    location      = "s3://${var.iceberg_s3_bucket}/${each.key}"
    input_format  = "org.apache.iceberg.mr.hive.HiveIcebergInputFormat"
    output_format = "org.apache.iceberg.mr.hive.HiveIcebergOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.iceberg.mr.hive.HiveIcebergSerDe"
    }

    dynamic "columns" {
      for_each = data.aws_glue_catalog_table.iceberg_source[each.key].storage_descriptor[0].columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = columns.value.comment
      }
    }
  }
}
```

### 5. Register S3 location with Lake Formation

```hcl
resource "aws_lakeformation_resource" "iceberg" {
  count = local.iceberg_enabled ? 1 : 0
  arn   = "arn:aws:s3:::${var.iceberg_s3_bucket}"
  # Uses AWSServiceRoleForLakeFormation — no explicit role needed
}
```

If this S3 location is already registered (BYOGDC ran earlier), this resource will
conflict. In that case, **import it** instead:

```sh
terraform import aws_lakeformation_resource.iceberg[0] \
  arn:aws:s3:::quilt-staging-icebergbucket-0epao5mayfko
```

### 6. Grant DataZone domain execution role LF admin on the new database

DataZone needs to be able to grant permissions to subscriber environment roles on
subscription approval. This requires granting the DataZone domain execution role
`ALL` with grant option on the new database and its tables.

```hcl
data "aws_datazone_domain" "raja" {
  identifier = var.datazone_domain_id  # already a variable or local
}

resource "aws_lakeformation_permissions" "iceberg_lf_dz_domain_db" {
  count    = local.iceberg_enabled ? 1 : 0
  principal = data.aws_datazone_domain.raja.execution_role

  permissions            = ["ALL"]
  permissions_with_grant_option = ["ALL"]

  database {
    name = aws_glue_catalog_database.iceberg_lf[0].name
  }
}

resource "aws_lakeformation_permissions" "iceberg_lf_dz_domain_tables" {
  for_each  = local.iceberg_enabled ? toset(local.iceberg_table_names) : toset([])
  principal = data.aws_datazone_domain.raja.execution_role

  permissions                   = ["ALL"]
  permissions_with_grant_option = ["ALL"]

  table {
    database_name = aws_glue_catalog_database.iceberg_lf[0].name
    name          = each.key
  }
}
```

### 7. Output

```hcl
output "iceberg_lf_database_name" {
  value = local.iceberg_enabled ? aws_glue_catalog_database.iceberg_lf[0].name : ""
}
```

## DataZone Data Product Registration (`seed_packages.py`)

The Terraform DataZone provider does not support Glue Table asset creation.
This follows the same pattern as `ensure_package_listing` / `ensure_project_package_grant`
already used for Quilt package assets — extend `seed_packages.py` with an
`ensure_iceberg_catalog_listing` step that runs after Terraform creates the LF-native database.

### DataZone API calls needed

**Create asset** (one per table, owned by owner project):

```python
datazone_client.create_asset(
    domainIdentifier=domain_id,
    owningProjectIdentifier=owner_project_id,
    name=table_name,                          # e.g. "package_revision"
    typeIdentifier="amazon.datazone.GlueTableAssetType",
    formsInput=[{
        "formName": "GlueTableForm",
        "content": json.dumps({
            "tableName": table_name,
            "databaseName": lf_database_name,  # from tf output
            "catalogId": account_id,
        }),
    }],
)
# then publish the asset revision:
datazone_client.create_asset_revision(...)   # or create_listing()
```

**Subscribe** (consumer project → each asset listing):

```python
# Same ensure_project_package_grant pattern, but for GlueTableAssetType listing IDs
```

### What to add to `seed_packages.py`

1. Read `iceberg_lf_database_name` from `tf-outputs.json` (new Terraform output).
2. Skip if empty (iceberg feature flag off).
3. For each of the 4 table names: call `create_asset` + publish listing in owner project.
4. For each subscriber project in seed config: call `create_subscription_request` +
   `accept_subscription_request` (same pattern as `_ensure_subscription_grant` in
   `sagemaker_gaps.py`).

The 4 table names and the subscriber projects are both derivable from `seed-config.yaml`
with no hardcoding.

## Verification

```sh
# Check LF grants on the environment role after subscription approval
aws lakeformation list-permissions \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::712023778557:role/raja-dz-env-users \
  --resource '{"Table":{"DatabaseName":"raja-standalone-iceberg-lf","TableWildcard":{}}}'
```

Expected: `SELECT` and `DESCRIBE` grants appear automatically — no `import_glue_db.py` needed.

## Tasks

- [ ] Add `iceberg_s3_bucket` variable to `variables.tf`
- [ ] Add locals, data sources, Glue resources, LF resource, and LF permission grants
      to `main.tf`; add output to `outputs.tf`
- [ ] Handle S3 location conflict: check if already registered, import if so
- [ ] `terraform apply` and confirm new database + tables exist in Glue console
- [ ] Register new tables as a DataZone data product (console)
- [ ] Subscribe from a second project and verify LF grants auto-provisioned
- [ ] Test: assume environment role via STS, run Athena query against new database
