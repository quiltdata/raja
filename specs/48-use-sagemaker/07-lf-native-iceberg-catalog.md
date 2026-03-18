# LF-Native Iceberg Catalog for Subscription-Gated Quilt Sessions

## Goal

Create a new Lake Formation-native Glue database that mirrors the four Quilt Iceberg
tables from the existing IAM-managed catalog. The point of this extension is to test
whether **native Glue table assets** in DataZone can drive the subscription approval
path that auto-provisions Lake Formation access to a subscriber environment role.

This is intentionally **new functionality**. The current repo only automates RAJA's
custom `QuiltPackage` assets. Glue table assets are a separate mechanism with different
API shapes and publication rules.

## Why the Existing Catalog Does Not Work

`icebergdatabase-v9cxuqnwjj5a` uses `IAM_ALLOWED_PRINCIPALS` (hybrid/IAM-managed mode).
DataZone can only auto-provision Lake Formation grants for **LF-native** databases.
A new database is created instead of migrating the existing one to avoid breaking
existing consumers.

## Scope

This document covers a POC path:

1. Create a new LF-native Glue database.
2. Mirror the existing Iceberg tables into it.
3. Register those tables in DataZone as **native Glue table assets**.
4. Subscribe from another project.
5. Verify that the subscriber environment role receives usable Lake Formation access.

This does **not** assume the current `src/raja/datazone/service.py` custom-asset
workflow can be reused unchanged. New helper code is expected.

## Key Limitation (POC Scope)

Iceberg tables track their current state via a `metadata_location` pointer in the
Glue catalog. The mirrored tables copy catalog state from the source tables **at
apply time**. After any Quilt write to the source tables, the LF-native copies may
have stale metadata until the mirroring step runs again.

**This is acceptable for the POC**. The goal is to prove the
subscription -> LF grant -> environment role access chain works.

**For production:** Quilt must write to the LF-native catalog directly, or a separate
synchronization mechanism must keep the mirrored catalog current.

## New Terraform Variable

```hcl
# variables.tf
variable "iceberg_s3_bucket" {
  description = "S3 bucket containing the Quilt Iceberg tables (without s3:// prefix)."
  type        = string
  default     = ""
}
```

## Terraform Changes

### 1. Locals

```hcl
locals {
  iceberg_enabled         = var.iceberg_s3_bucket != ""
  iceberg_source_database = "icebergdatabase-v9cxuqnwjj5a"
  iceberg_native_database = "${var.stack_name}-iceberg-lf"
  iceberg_table_names     = [
    "package_entry",
    "package_manifest",
    "package_revision",
    "package_tag",
  ]
}
```

The table list is hardcoded for this POC. It is **not** currently derivable from
`seed-config.yaml`.

### 2. Read source database and source tables

```hcl
data "aws_glue_catalog_database" "iceberg_source" {
  count = local.iceberg_enabled ? 1 : 0
  name  = local.iceberg_source_database
}

data "aws_glue_catalog_table" "iceberg_source" {
  for_each      = local.iceberg_enabled ? toset(local.iceberg_table_names) : toset([])
  database_name = local.iceberg_source_database
  name          = each.key
}
```

### 3. Create a new LF-native Glue database

```hcl
resource "aws_glue_catalog_database" "iceberg_lf" {
  count = local.iceberg_enabled ? 1 : 0
  name  = local.iceberg_native_database
}
```

Do not set `create_table_default_permission`. The intent is for Lake Formation, not
`IAM_ALLOWED_PRINCIPALS`, to be authoritative.

### 4. Mirror the source tables conservatively

The mirrored tables should copy the source table definitions as closely as the
Terraform provider allows. Do **not** rebuild them from only table name + columns.

At minimum, preserve:

- Iceberg-related `parameters`, including `metadata_location`
- `storage_descriptor.location`
- column definitions
- partition keys, if present
- table type required for Iceberg interoperability

Implementation note:

- Prefer copying the source `storage_descriptor.location` from Glue rather than
  reconstructing `s3://${var.iceberg_s3_bucket}/${each.key}`.
- If the provider cannot express all source-table fields needed for Athena/Glue to
  treat the mirrored table as a valid Iceberg table, stop and validate with a single
  table first before scaling to all four.

Illustrative shape:

```hcl
resource "aws_glue_catalog_table" "iceberg_lf" {
  for_each      = local.iceberg_enabled ? toset(local.iceberg_table_names) : toset([])
  database_name = aws_glue_catalog_database.iceberg_lf[0].name
  name          = each.key
  table_type    = data.aws_glue_catalog_table.iceberg_source[each.key].table_type

  parameters = data.aws_glue_catalog_table.iceberg_source[each.key].parameters

  storage_descriptor {
    location      = data.aws_glue_catalog_table.iceberg_source[each.key].storage_descriptor[0].location
    input_format  = data.aws_glue_catalog_table.iceberg_source[each.key].storage_descriptor[0].input_format
    output_format = data.aws_glue_catalog_table.iceberg_source[each.key].storage_descriptor[0].output_format

    dynamic "columns" {
      for_each = data.aws_glue_catalog_table.iceberg_source[each.key].storage_descriptor[0].columns
      content {
        name    = columns.value.name
        type    = columns.value.type
        comment = columns.value.comment
      }
    }
  }

  dynamic "partition_keys" {
    for_each = data.aws_glue_catalog_table.iceberg_source[each.key].partition_keys
    content {
      name    = partition_keys.value.name
      type    = partition_keys.value.type
      comment = partition_keys.value.comment
    }
  }
}
```

The exact field set may need adjustment to match the AWS provider schema. The key
requirement is fidelity to the source Glue definition, not brevity.

Do not copy `ser_de_info` unless a real source table proves it is present and needed.
The current Quilt Iceberg tables can omit it entirely, and indexing into
`ser_de_info[0]` is not safe.

### 5. Register the S3 location with Lake Formation

```hcl
resource "aws_lakeformation_resource" "iceberg" {
  count = local.iceberg_enabled ? 1 : 0
  arn   = "arn:aws:s3:::${var.iceberg_s3_bucket}"
}
```

This may already exist if the S3 location was registered by a previous manual or
BYOGDC flow. In that case, import it into Terraform state instead of trying to create
it again.

```sh
terraform import 'aws_lakeformation_resource.iceberg[0]' \
  arn:aws:s3:::quilt-staging-icebergbucket-0epao5mayfko
```

### 6. Grant the DataZone domain execution role authority to re-grant on the new catalog

DataZone needs permission on the LF-native database and tables so subscription
approval can result in grants to subscriber environment roles.

In this repo, Terraform already owns the domain as `aws_datazone_domain.raja`. Use
that resource directly unless the domain ownership model changes.

```hcl
resource "aws_lakeformation_permissions" "iceberg_lf_dz_domain_db" {
  count     = local.iceberg_enabled ? 1 : 0
  principal = aws_datazone_domain.raja.domain_execution_role

  permissions                   = ["ALL"]
  permissions_with_grant_option = ["ALL"]

  database {
    name = aws_glue_catalog_database.iceberg_lf[0].name
  }
}

resource "aws_lakeformation_permissions" "iceberg_lf_dz_domain_tables" {
  for_each  = local.iceberg_enabled ? toset(local.iceberg_table_names) : toset([])
  principal = aws_datazone_domain.raja.domain_execution_role

  permissions                   = ["ALL"]
  permissions_with_grant_option = ["ALL"]

  table {
    database_name = aws_glue_catalog_database.iceberg_lf[0].name
    name          = each.key
  }
}
```

Open question:

- If DataZone requires extra Lake Formation permissions beyond this for managed
  subscription grants on Glue table assets, discover that empirically and record it.

### 7. Output

```hcl
output "iceberg_lf_database_name" {
  value = local.iceberg_enabled ? aws_glue_catalog_database.iceberg_lf[0].name : ""
}
```

## DataZone Registration

This is **not** the same mechanism as RAJA's custom `QuiltPackage` assets.

AWS now documents the native Glue-table API shape directly, and this domain exposes
the required system types.

Confirmed in this account/domain:

- asset type: `amazon.datazone.GlueTableAssetType`
- asset type revision in this domain: `24`
- required form: `GlueTableForm`
- form type: `amazon.datazone.GlueTableFormType`
- form type revision in this domain: `13`

Implementation path:

1. `create_asset(...)` with `amazon.datazone.GlueTableAssetType`
2. `create_listing_change_set(... action="PUBLISH")`
3. `create_subscription_request(...)`
4. `accept_subscription_request(...)`

The current repo only proves out custom assets with `create_asset` +
`create_listing_change_set`. That code remains a useful boto3 pattern, but Glue table
assets need their own payload builder and idempotency strategy.

### Confirmed DataZone asset shape

Per AWS's API reference, the Glue table asset can be created directly with
`create_asset`.

Illustrative request shape:

```python
datazone_client.create_asset(
    domainIdentifier=domain_id,
    owningProjectIdentifier=owner_project_id,
    name=table_name,
    externalIdentifier=f"glue://{account_id}/{region}/{database_name}/{table_name}",
    typeIdentifier="amazon.datazone.GlueTableAssetType",
    formsInput=[{
        "formName": "GlueTableForm",
        "typeIdentifier": "amazon.datazone.GlueTableFormType",
        "typeRevision": "13",
        "content": json.dumps({
            "databaseName": database_name,
            "tableName": table_name,
            "catalogId": account_id,
            "region": region,
            "tableArn": f"arn:aws:glue:{region}:{account_id}:table/{database_name}/{table_name}",
            "columns": [
                {
                    "columnName": col["Name"],
                    "dataType": col["Type"],
                    "description": col.get("Comment", ""),
                }
                for col in glue_table["StorageDescriptor"]["Columns"]
            ],
        }),
    }],
)
```

Then publish it:

```python
datazone_client.create_listing_change_set(
    domainIdentifier=domain_id,
    entityIdentifier=asset_id,
    entityRevision=asset_revision,
    entityType="ASSET",
    action="PUBLISH",
)
```

### Remaining discovery before coding

The unknowns are now narrower:

1. Does `externalIdentifier` behave cleanly for Glue table assets in this domain?
2. Is `glue://<account>/<region>/<database>/<table>` a good enough stable identifier
   for idempotent reruns?
3. After publication, does subscription approval on these manually created Glue table
   assets actually drive the LF grant behavior we want?

Code can proceed with a **single-table spike** before generalizing to all four.

### Suggested automation boundary

If boto3 automation is needed, add a **new helper** for Glue table assets rather than
forcing this through the existing Quilt package abstraction.

Good options:

- extend `scripts/seed_packages.py` with a separate Glue-table branch, or
- create `scripts/seed_glue_tables.py` and keep it isolated from package seeding

Either way, the helper should:

1. Read `iceberg_lf_database_name` from `infra/tf-outputs.json`.
2. Skip when the output is empty.
3. Use the hardcoded POC table list.
4. Create or discover one DataZone asset/listing per table in the owner project.
5. Create and accept subscription requests for the target subscriber projects.
6. Record enough stable metadata to make reruns idempotent.

## Verification

### Infrastructure

```sh
aws glue get-database --name raja-standalone-iceberg-lf
aws glue get-table --database-name raja-standalone-iceberg-lf --name package_tag
```

### Lake Formation grants

```sh
aws lakeformation list-permissions \
  --principal DataLakePrincipalIdentifier=arn:aws:iam::712023778557:role/raja-dz-env-users \
  --resource '{"Table":{"DatabaseName":"raja-standalone-iceberg-lf","TableWildcard":{}}}'
```

Expected after subscription approval:

- `SELECT`
- `DESCRIBE`

### Query validation

Assume the subscriber environment role and run a real Athena query against the new
database. A metadata-only permission check is not enough; the POC only succeeds if
Athena can read at least one mirrored table.

## Tasks

- [ ] Add `iceberg_s3_bucket` to `variables.tf`
- [ ] Add LF-native Glue database, mirrored tables, LF S3 registration, LF grants, and
      `iceberg_lf_database_name` Terraform output
- [ ] Import existing LF S3 registration into state if it already exists
- [ ] `terraform apply` and confirm the new database + tables exist in Glue
- [ ] Validate one mirrored table in Athena before scaling confidence to all four
- [ ] Discover the real DataZone Glue-table asset publication flow for this domain
- [ ] Add isolated automation for Glue-table registration/subscription only after that
      flow is confirmed
- [ ] Subscribe from a second project and verify LF grants auto-provision
- [ ] Assume the subscriber environment role and run Athena against the mirrored table
