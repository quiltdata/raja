# LF-Native Iceberg Catalog — H1/H2/H4 Tests

## Context

This spec follows `08-lf-native-iceberg-catalog-blockers.md`. That document
confirmed that accepted subscriptions to manually created `GlueTableAssetType`
assets did not produce Lake Formation grants to subscriber environment roles.
Four hypotheses (H1–H4) were left open. This spec records the work done to
probe them and where that work landed.

---

## What Was Done

### Step 1 — H2 test: Owner env role LF grants (Terraform)

**File:** `infra/terraform/main.tf`

Added three new `aws_lakeformation_permissions` blocks for the owner Lakehouse
environment role (`aws_iam_role.datazone_environment_owner`), mirroring the
existing domain-execution-role grants:

- `iceberg_lf_owner_env_data_location` — `DATA_LOCATION_ACCESS` with grant
  option, one per table S3 location
- `iceberg_lf_owner_env_db` — `ALL` with grant option on the database
- `iceberg_lf_owner_env_tables` — `ALL` with grant option per table

**Rationale:** DataZone may require the publishing environment's IAM role to
hold grantable LF permissions before it can delegate grants to subscriber
environment roles. Only the domain execution role was previously granted.

### Step 2 — H4 probe: `--inspect` mode in `seed_glue_tables.py`

**File:** `scripts/seed_glue_tables.py`

Added `--inspect` flag. When passed, the script reads saved seed state and
calls `datazone.list_subscription_grants(subscribedListingId=...)` for each
table listing. It then:

- Reports whether any grant objects exist at all
- Shows per-grant status (`GRANTED` / `GRANT_FAILED` / etc.) and per-asset
  failure messages
- Cross-references subscription request IDs against grant object subscription
  IDs to surface orphaned requests

### Step 3 — H1 test: DataZone Glue data source in `sagemaker_gaps.py`

**File:** `scripts/sagemaker_gaps.py`

Added `_ensure_iceberg_lf_data_source()`, called at the end of the main
function after environments are discovered. It:

1. Reads `iceberg_lf_database_name` from tf-outputs.json (skips if empty)
2. Gets the owner project's Lakehouse environment ID from the discovered
   `environment_ids["owner"]`
3. Calls `datazone.list_data_sources()` to check for an existing data source
   named `{database_name}-datasource`
4. Creates it via `datazone.create_data_source()` with `type="GLUE"`,
   `publishOnImport=True`, and a relational filter covering all tables in the
   LF-native database
5. Calls `datazone.start_data_source_run()` to trigger an initial import

---

## What Was Tested

`./poe test-all` was run on the standard dev deploy (the one without
`iceberg_s3_bucket` set). All 55 integration tests passed.

---

## Where It Failed / What Was Blocked

### Iceberg feature not enabled in dev deploy

`iceberg_s3_bucket` is not set in the dev Terraform variables, so:

- `iceberg_lf_database_name` is empty in tf-outputs.json
- `seed_glue_tables.py` prints `skipped (iceberg_lf_database_name is empty)`
  and exits
- `sagemaker_gaps.py` prints `skipped (iceberg_lf_database_name not set in
  outputs)` and skips data source creation

Neither the H2 Terraform grants nor the H1 data source were exercised because
the Iceberg stack was not deployed in this session.

### H4 confirmed: DataZone never attempted LF grant fulfillment

`--inspect` was run against the existing seed state from the prior POC session
(the deploy that had `raja-standalone-iceberg-lf` and accepted subscriptions).
Output:

```
package_entry   ✗ No subscription grant objects found
package_manifest ✗ No subscription grant objects found
package_revision ✗ No subscription grant objects found
package_tag     ✗ No subscription grant objects found

RESULT: DataZone never initiated LF grant fulfillment for those subscriptions.
This confirms H4 and narrows root cause to H1 or H2.
```

This is not a failed grant — no grant object was ever created. DataZone's LF
subscription machinery never fired at all for manually created Glue assets.

### Lakehouse environments missing in dev deploy

`sagemaker_gaps.py` also revealed:

```
Environment owner: raja-alpha-env (skipped — project profile '4n0danlvurs0br'
has no raja-registry env config; re-create project with raja-default-profile)
Environment owner: raja-alpha-env (missing)
```

The owner/users/guests projects exist under a different profile
(`4n0danlvurs0br`) rather than `raja-default-profile`. Lakehouse environments
were never created for this profile. Even if `iceberg_s3_bucket` were set,
`_ensure_iceberg_lf_data_source` would skip because `owner_environment_id`
would be empty.

---

## Net State

| Hypothesis | Status |
|---|---|
| H1 (data-source linkage) | **Untested** — Iceberg stack not deployed in this session |
| H2 (publisher principal) | **Untested** — Iceberg stack not deployed in this session |
| H3 (asset payload incomplete) | **Untested** |
| H4 (no grant attempt at all) | **Confirmed** — `--inspect` shows zero grant objects |

The new code is correct and in place. Testing H1 and H2 requires a deploy with
`iceberg_s3_bucket` set, pointing at the Quilt bucket that contains the
Iceberg tables, so that `raja-standalone-iceberg-lf` is created.

---

## Prerequisites for Next Test Run

1. Set `iceberg_s3_bucket` in `infra/terraform/terraform.tfvars` (or the
   equivalent `.env`-driven variable) to the Quilt S3 bucket name (without
   `s3://` prefix).
2. Run `terraform apply` — this will:
   - Create the LF-native database and tables
   - Apply the H2 owner-env-role LF grants
3. Run `python -m scripts.seed_glue_tables` to publish table assets and
   create subscriptions.
4. Run `python -m scripts.sagemaker_gaps` — this will create the H1 Glue data
   source and start the import run (requires owner Lakehouse environment to
   exist first; see environment profile issue above).
5. Run `python -m scripts.seed_glue_tables --inspect` to check grant state.
6. Check LF permissions directly:
   ```bash
   aws lakeformation list-permissions \
     --resource '{"Table":{"DatabaseName":"raja-standalone-iceberg-lf","Name":"package_tag"}}' \
     --query 'PrincipalResourcePermissions[].Principal'
   ```

## Open Pre-condition

The owner Lakehouse environment issue (`4n0danlvurs0br` profile) must be
resolved before the H1 data source can be created via `sagemaker_gaps.py`.
Options:

- Re-create the owner project under `raja-default-profile` so the Lakehouse
  environment blueprint is available, **or**
- Hardcode the existing Lakehouse environment ID (from the prior POC session:
  `6i8xx7hn1vt2qv`) as a fallback in `_ensure_iceberg_lf_data_source`.
