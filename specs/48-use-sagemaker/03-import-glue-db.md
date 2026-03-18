# Import Existing Glue Database into DataZone Projects

## Goal

Make the existing Iceberg Glue database (us-east-1) visible and queryable from all
three DataZone project environments — Alpha, Bio, and Compute — by granting their
environment role ARNs Lake Formation access to the tables.

## Why

The DataZone projects defined in `seed-config.yaml` have environment roles but those
roles currently have no Lake Formation permissions on the Iceberg tables. Without this,
project members cannot query the Iceberg tables from within the SageMaker Unified Studio
IDE even if they hold a valid DataZone subscription grant.

## Tool

`bring_your_own_gdc_assets.py` from the Unified Studio migration repo performs the
one-time setup per project role:

1. Detects whether the Glue database is IAM-managed; if so, creates a Lake Formation
   opt-in for the project role (hybrid mode).
2. Enumerates all tables in the database.
3. Registers each table's S3 location with Lake Formation (hybrid mode, using the
   service-linked role if no explicit registration role is provided).
4. Creates a Lake Formation opt-in per table for the project role.
5. Grants `ALL` + `ALL WITH GRANT OPTION` on each table to the project role.

## Inputs from tf-outputs.json

| Project | tf-outputs key | Role ARN |
| ------- | -------------- | -------- |
| Alpha (owner) | `datazone_owner_environment_role_arn` | `arn:aws:iam::712023778557:role/raja-dz-env-owner` |
| Bio (users) | `datazone_users_environment_role_arn` | `arn:aws:iam::712023778557:role/raja-dz-env-users` |
| Compute (guests) | `datazone_guests_environment_role_arn` | `arn:aws:iam::712023778557:role/raja-dz-env-guests` |

**Glue database:** `icebergdatabase-v9cxuqnwjj5a` (catalog `712023778557`, region `us-east-1`)

**S3 bucket:** `s3://quilt-staging-icebergbucket-0epao5mayfko/`

**Tables:**

| Table | S3 Location |
| ----- | ----------- |
| `package_entry` | `s3://quilt-staging-icebergbucket-0epao5mayfko/package_entry` |
| `package_manifest` | `s3://quilt-staging-icebergbucket-0epao5mayfko/package_manifest` |
| `package_revision` | `s3://quilt-staging-icebergbucket-0epao5mayfko/package_revision` |
| `package_tag` | `s3://quilt-staging-icebergbucket-0epao5mayfko/package_tag` |

**Note:** The database uses `IAM_ALLOWED_PRINCIPALS` with `ALL` permissions, confirming
it is IAM-managed (not Lake Formation native). The script will take the hybrid mode path.

## Wrapper Script

Create `scripts/import_glue_db.py` in the raja repo that:

- Reads the three environment role ARNs from `scripts/tf_outputs.py`
- Calls the private functions from `bring_your_own_gdc_assets.py` directly
  (not `byogdc_main()` — that calls `_parse_args()` and is not importable as a library)
- Runs the import for each project role in sequence
- Accepts `--database-name` and `--dry-run` flags

The script must add the byogdc directory to `sys.path` before importing. That directory
is `../Unified-Studio-for-Amazon-Sagemaker/migration/bring-your-own-gdc-assets/`
relative to the raja repo root (sibling repo on disk), or the path can be made
configurable via `--byogdc-path`.

**Dry-run:** `bring_your_own_gdc_assets.py` has no `--dry-run` flag. The wrapper's
dry-run mode should print the operations it would perform (role ARNs, database, tables)
without calling any boto3 LakeFormation or Glue APIs.

**Per-role call sequence** (mirrors `byogdc_main` minus `_parse_args`):

```python
import boto3, sys
sys.path.insert(0, byogdc_dir)
from bring_your_own_gdc_assets import (
    _check_database_managed_by_iam_access_and_enable_opt_in,
    _get_all_tables_for_a_database,
    _check_and_register_location,
    _check_table_managed_by_iam_access_and_enable_opt_in,
    _grant_permissions_to_table,
)

for role_arn in [owner_arn, users_arn, guests_arn]:
    session = boto3.Session(region_name="us-east-1")
    lf  = session.client("lakeformation")
    glue = session.client("glue")
    _check_database_managed_by_iam_access_and_enable_opt_in(db, role_arn, lf)
    tables = _get_all_tables_for_a_database(db, glue)
    _check_and_register_location(tables, None, lf)  # uses service-linked role
    for t in tables:
        _check_table_managed_by_iam_access_and_enable_opt_in(db, t["Name"], role_arn, lf)
        _grant_permissions_to_table(role_arn, db, t["Name"], lf)
```

### Interface

```sh
uv run python scripts/import_glue_db.py \
  [--database-name icebergdatabase-v9cxuqnwjj5a] \
  [--byogdc-path ../Unified-Studio-for-Amazon-Sagemaker/migration/bring-your-own-gdc-assets] \
  [--dry-run]
```

No `--project-role-arn` needed — all three are read automatically from tf-outputs.

## Prerequisites

- Caller must have IAM permissions: `lakeformation:*`, `glue:GetTable`,
  `glue:GetTables`, `glue:GetDatabase`
- The Glue database `icebergdatabase-v9cxuqnwjj5a` must exist in us-east-1 in account
  `712023778557` (confirmed: 4 tables present)
- `infra/tf-outputs.json` must be present (run `./poe deploy` or
  `cd infra/terraform && terraform output -json > ../tf-outputs.json`)

## Tasks

- [ ] Write `scripts/import_glue_db.py` wrapping `bring_your_own_gdc_assets.py`
      functions for the three project roles from tf-outputs
- [ ] Run dry-run against `icebergdatabase-v9cxuqnwjj5a` to confirm opt-in and grant
      logic before making changes
- [ ] Run for real: Alpha → Bio → Compute
- [ ] Verify each project role can query at least one table via
      `aws glue get-table --database-name icebergdatabase-v9cxuqnwjj5a --name package_tag`
      with `--role-arn` override (or via SageMaker Studio IDE)

## Notes

- The script is idempotent: re-running it is safe (opt-ins and grants are checked
  before creation; S3 registration raises if already registered but the wrapper
  catches that).
- `--iam-role-arn-lf-resource-register` is left unset, so S3 locations are
  registered using `AWSServiceRoleForLakeFormation` (the default service-linked
  role). This avoids needing a separate registration role.
- This is a one-time setup step per database. New tables added to
  `icebergdatabase-v9cxuqnwjj5a` later will need a re-run to pick up new grants.
