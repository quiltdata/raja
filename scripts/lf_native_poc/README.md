# LF-Native Import POC

This folder contains the isolated probes that turned the LF-native
Iceberg/DataZone blocker from guesswork into a reproducible path.

Entrypoints:

- `python -m scripts.lf_native_poc.package_tag_import_poc`
- `python -m scripts.lf_native_poc.create_throwaway_subscriber`

## What Actually Worked

- Do not use manual `create_asset` Glue-table assets for LF-native tables.
  They can reach `ACCEPTED` subscriptions without producing managed LF grants.
- Use a real DataZone-managed Glue import and subscribe against the imported
  listings, not the manual ones.
- Give the owner Glue data source role Lake Formation access to the database,
  every table, and every table location. This was the missing step that let the
  data source import all four tables instead of only `package_tag`.
- Re-run the Glue data source import whenever DataZone has imported fewer than
  the expected four tables.
- Keep the default Lakehouse blueprint healthy. Fresh subscriber projects only
  worked after fixing the live Tooling and Lakehouse blueprint configs.
- The successful LF fulfillment shape is not a direct table grant to the
  subscriber environment role ARN. DataZone writes conditional `SELECT` grants
  on `712023778557:IAMPrincipals` scoped by `context.datazone.projectId`.

## Final Working Path

1. Mirror the four Iceberg tables into `raja-standalone-iceberg-lf`.
2. Ensure the owner project has a DataZone Glue data source for that database.
3. Grant the data source role LF `ALL` on the database and tables plus
   `DATA_LOCATION_ACCESS` on all table locations.
4. Start or restart the DataZone Glue import.
5. Wait for imported listings for:
   - `package_entry`
   - `package_manifest`
   - `package_revision`
   - `package_tag`
6. Subscribe `bio` and `compute` against those imported listings.
7. Verify LF conditional `SELECT` grants exist for both subscriber project IDs.

## Working Evidence

- Imported listings now used by the main seed flow:
  - `package_entry` -> `cll99ezfwkw8pz`
  - `package_manifest` -> `6q5wgwn4bjha5j`
  - `package_revision` -> `apj78613rljtpj`
  - `package_tag` -> `5za88zhymk4qzr`
- Completed DataZone grant objects now exist for:
  - `package_entry`
  - `package_manifest`
  - `package_revision`
  - `package_tag`
- Final LF state now includes conditional `SELECT` grants for both subscriber
  project IDs:
  - `bm7eqh5dc6olrb`
  - `b3byg401pnpjjb`

## Cleanup

- Delete the throwaway subscriber project `60st0m21xz0a3r` if it is no longer
  needed.
- Old failed `package_tag` grant records from earlier experiments still exist in
  DataZone history. They do not block the working path, but they are noise when
  inspecting grant history.
- Terraform still does not fully own the live DataZone domain role selection if
  `ignore_changes` remains on `domain_execution_role` / `service_role`. That is
  worth reconciling separately so the production-shaped role choice is durable.
