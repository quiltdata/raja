# LF-Native Import POC

This folder contains isolated probes for the LF-native Iceberg/DataZone blocker.

Current entrypoint:

- `python -m scripts.lf_native_poc.package_tag_import_poc`
- `python -m scripts.lf_native_poc.create_throwaway_subscriber`

The `package_tag` POC does four things against the deployed stack:

1. Resolves the owner Glue data source for `raja-standalone-iceberg-lf`
2. Grants Lake Formation permissions to the data source's `dataAccessRole`
3. Re-runs the DataZone Glue import and looks for an imported `package_tag` asset
4. Subscribes one subscriber project to that imported asset and inspects:
   - DataZone subscription grant objects
   - Lake Formation permissions for the subscriber environment role

This is intentionally isolated from `scripts/seed_glue_tables.py` so we can test
managed-import behavior without changing the main seed flow.

`create_throwaway_subscriber` creates a new DataZone project with a profile that
actually includes the Lakehouse environment configuration, waits for the
auto-created environments, and prints the new project/environment IDs plus the
subscriber environment role ARN.

## What Worked

- Use a real DataZone-managed Glue import. Manual `create_asset` Glue-table
  assets reached `ACCEPTED` subscriptions but did not produce managed LF grant
  objects.
- Subscribe against the imported `package_tag` listing `5za88zhymk4qzr`, not the
  manual listing.
- Patch the live domain to use the Terraform-owned execution and service roles.
- Ensure the live DataZone manage-access role is the Lake Formation actor:
  `arn:aws:iam::712023778557:role/service-role/AmazonSageMakerManageAccess-us-east-1-dzd-6w14ep5r5owwh3`
- Fix broken DataZone environment blueprint configs so fresh subscriber
  projects can actually provision:
  - `cjegf7f6kky6w7` Tooling needed regional parameters and the standard
    SageMaker provisioning/manage-access roles.
  - `d6y5smpdi8x9lz` Lakehouse needed an explicit provisioning role and
    manage-access role.
- Use a brand-new throwaway subscriber project and environment after those
  blueprint fixes.
- Successful fulfillment shape is a conditional LF `SELECT` grant on
  `712023778557:IAMPrincipals` scoped by `context.datazone.projectId`, not a
  direct table grant to the subscriber environment role ARN.

## Working Evidence

- Throwaway project: `60st0m21xz0a3r`
- Throwaway Lakehouse environment: `bg97z6668zws2v`
- Throwaway subscriber env role:
  `arn:aws:iam::712023778557:role/datazone_usr_role_60st0m21xz0a3r_4e7kk4zcg1wmuf`
- Imported-listing grant object: `509qbg4b2lj1rb`
- Final DataZone state:
  - grant `status = COMPLETED`
  - asset `status = GRANTED`
- Final LF state:
  - conditional `SELECT` grant exists for
    `context.datazone.projectId=="60st0m21xz0a3r"`
