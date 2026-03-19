# LF-Native Iceberg Catalog Blockers

## Goal

Document the remaining blocker after implementing the LF-native Iceberg catalog
POC on March 18, 2026: DataZone accepts subscriptions to manually created Glue
table assets, but subscriber Lakehouse environment roles still do not receive
usable Lake Formation grants.

## Current State

A new Glue database `raja-standalone-iceberg-lf` exists and mirrors the four
Quilt Iceberg tables:

- `package_entry`
- `package_manifest`
- `package_revision`
- `package_tag`

These tables are published into DataZone as
`amazon.datazone.GlueTableAssetType` assets and subscription requests were
created and reached `ACCEPTED`.

## Facts

### Infrastructure facts

- The mirrored Glue database and tables were created successfully.
- The mirrored tables point at the expected Quilt Iceberg metadata and S3
  locations.
- The S3 table locations were already registered in Lake Formation at
  per-table prefixes.
- The mirrored tables now show `IsRegisteredWithLakeFormation = true`.
- The database initially came up with `IAM_ALLOWED_PRINCIPALS`; this had to be
  explicitly removed after create.
- Lake Formation grants exist for:
  - `arn:aws:iam::712023778557:role/service-role/AmazonSageMakerDomainExecution`
  - the operator principal used during apply
- Lake Formation grants do not exist for the subscriber environment roles:
  - `arn:aws:iam::712023778557:role/datazone_usr_role_bm7eqh5dc6olrb_5vadl88wuqs59j`
  - `arn:aws:iam::712023778557:role/datazone_usr_role_b3byg401pnpjjb_3zjkma8hy45u07`

### DataZone facts

- The domain exposes:
  - asset type `amazon.datazone.GlueTableAssetType`
  - asset type revision `24`
  - form `GlueTableForm`
  - form type `amazon.datazone.GlueTableFormType`
  - form type revision `13`
- Manual `create_asset` works for Glue table assets in this domain.
- The `GlueTableForm` schema rejects a `description` field inside `columns`.
- Published listings were successfully created for all four mirrored tables.
- Subscription requests for users/guests projects were created.
- In some cases, subscription requests auto-transitioned to `ACCEPTED` before
  an explicit accept call.
- Despite accepted subscriptions, no subscriber LF grants appeared on the
  mirrored database or tables.

### Environment facts

- Owner, users, and guests projects each have active `Lakehouse Database`
  environments.
- Each such environment has its own generated `userRoleArn`.
- The owner project has a Glue data source configured against its own generated
  Glue DB:
  - data source id `6i8xx7hn1vt2qv`
  - type `GLUE`
  - data access role
    `arn:aws:iam::712023778557:role/datazone_usr_role_ag00w9am11jcx3_cgaw0vcx71emnb`
- That data source is not pointing at `raja-standalone-iceberg-lf`.

## Confirmed Blocker

Accepted subscriptions to manually created Glue table assets are not, in this
POC, resulting in the downstream DataZone-managed fulfillment step that should
materialize Lake Formation grants to subscriber Lakehouse environment roles.

Important distinction:

- `ACCEPTED` confirms the subscription decision was recorded.
- It does not, by itself, prove that DataZone completed environment grant
  fulfillment.
- AWS documents that for managed Glue assets, approved subscriptions should be
  automatically added to existing data lake environments in the subscriber
  project.
- AWS separately documents an explicit `Add grant` action for cases where a new
  environment is added later and needs access to an already-approved
  subscription.
- AWS also documents optional row/column filter flows, but those are an
  additional narrowing mechanism, not the default prerequisite for ordinary
  full-table sharing.

Therefore the current blocker is more precise than "acceptance is insufficient":
the system is reaching subscription acceptance, but not the expected managed
Lake Formation grant fulfillment for existing subscriber environments.

## Hypotheses

### H1: Missing data-source linkage

DataZone may only auto-manage LF grants for Glue assets that originate from a
registered/published Glue data source import path, not arbitrary manual
`create_asset` calls.

Why this is plausible:

- The owner project already has a GLUE data source and a dedicated data access
  role.
- AWS documentation for managed Glue assets is written around DataZone-managed
  Glue publication, not arbitrary manual asset creation.
- The manually created assets are subscribable, but may not be recognized by
  DataZone as eligible for managed fulfillment.

### H2: Wrong grant principal on publisher side

The critical grantable principal may not be the domain execution role alone.
DataZone may require the publishing environment's Glue data access role or
Lakehouse environment role to hold grantable LF permissions on the database,
tables, and data locations.

Why this is plausible:

- The owner Lakehouse data source explicitly uses
  `datazone_usr_role_ag00w9am11jcx3_cgaw0vcx71emnb`.
- Granting only the domain execution role did not produce subscriber grants.

### H3: Manual asset payload is incomplete

The manually created Glue assets may be missing internal metadata that DataZone
uses to connect an asset to the managed Glue subscription workflow.

Why this is plausible:

- The assets were publishable and subscribable, but no managed LF side effects
  occurred.
- The `DataSourceReferenceForm` exists in the asset type, but was not
  populated.
- The owner Glue data source has never imported these tables.
- A manually created asset may not carry the same source lineage metadata as an
  imported managed Glue asset.

### H4: Environment attachment step is required, but is not being triggered

Accepted subscriptions may not automatically attach to existing Lakehouse
environments for these manually created assets, even though the request status
is `ACCEPTED`.

Why this is plausible:

- The subscriber roles received no LF permissions.
- AWS documentation distinguishes:
  - automatic fulfillment to existing subscriber data lake environments for
    managed Glue assets
  - explicit `Add grant` for environments added after approval
- The current POC may be bypassing or failing the automatic fulfillment path.

### H5: Filters are not the blocker, but grant mode may still matter

The missing step is unlikely to be "define filters", but DataZone may still use
different fulfillment logic when a grant is represented as a filtered access
path versus a full-table share.

Why this is plausible:

- AWS documents filters as an explicit additional access-grant mode for managed
  assets.
- No filter configuration was used here.
- The failure is occurring before any subscriber LF permissions appear at all,
  which suggests the issue is more fundamental than row/column scoping.

## Non-Goals

This follow-on work should not:

- change Quilt's writer path yet
- redesign the RAJA package asset flow
- optimize sync of Iceberg metadata pointers
- generalize beyond the four POC tables

## Suggested Next Steps

1. Prove whether DataZone requires data-source-backed publication.
   - Create a real owner-project Glue data source/import path for
     `raja-standalone-iceberg-lf`.
   - Re-publish one table through that managed path.
   - Re-run one subscription and inspect LF grants.

2. Identify the true publisher-side grant principal.
   - Compare behavior when grantable LF permissions are given to:
     - domain execution role
     - owner Lakehouse environment `userRoleArn`
     - owner Glue data source `dataAccessRole`
   - Record the minimal working principal set.

3. Test whether `DataSourceReferenceForm` is required.
   - Inspect a Glue asset produced by a real DataZone Glue import.
   - Compare its forms to the manually created asset.
   - Especially check `DataSourceReferenceForm` and any internal forms tied to
     source lineage.

4. Verify environment attachment behavior explicitly.
   - After accepted subscription, inspect whether the asset appears as granted
     to subscriber Lakehouse environments.
   - If not, determine whether:
     - the asset is ineligible for automatic fulfillment
     - an explicit `Add grant` action is required
     - the `Add grant` path is only supported for managed imported Glue assets

5. Reduce scope to a single-table spike.
   - Use only `package_tag` until LF grants reach one subscriber role and
     Athena can query it.

6. Confirm whether filters change behavior.
   - Test one managed Glue-table subscription with no filters.
   - Test one managed Glue-table subscription with row/column filters only if
     the no-filter path still fails.
   - Treat filters as a diagnostic branch, not the default fix.

7. Only after the above succeeds, update automation.
   - Keep the current manual asset seeding isolated.
   - Replace it with the managed publication flow if that proves necessary.

## Success Criteria for the Next Spec

The next implementation is only complete when all of the following are true for
at least one mirrored table:

- subscription status is `ACCEPTED`
- subscriber Lakehouse environment role has LF permissions on the mirrored
  DB/table
- Athena can query the table while assuming the subscriber environment role
- no `IAM_ALLOWED_PRINCIPALS` permissions remain on the mirrored catalog
  resources

## Recommended Direction

Bias toward proving H1 and H2 first:

- managed Glue data source publication
- owner data access role grantability

Then validate H4:

- whether existing subscriber Lakehouse environments should already have been
  fulfilled automatically
- whether `Add grant` is only relevant to post-approval environment additions

Do not treat filters as the primary explanation unless a managed imported Glue
asset also fails without them. The current evidence points first to managed
fulfillment eligibility and/or publisher-side LF grantability.
