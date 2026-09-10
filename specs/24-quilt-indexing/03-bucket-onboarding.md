# Bucket Onboarding as Ingest

**Status:** proposal, no code. **Date:** 2026-09-10. **Companion to:** *Quilt Indexing and Search Redesign* and *Auto-Packaging from S3 Folders*. **Scope:** what happens between "connect this bucket to Quilt" and "this bucket's data is organized into packages with metadata", designed so that the first import of a bucket discovers its conventions once and turns them into standing packaging rules.

# Summary

Connecting a bucket today means wiring notifications, creating search indexes, and starting a crawl. The admin types a title and a description; nothing looks at what is in the bucket, and packages only appear if someone already made them. The proposal turns connection into a staged **ingest**: inventory the bucket in hours from S3 Metadata, run one analytical pass over that inventory to build a **prefix profile** of every folder, detect **families** of sibling folders that share a shape and derive **path templates** from them, **probe** a sample of each family with the format recognizers and metadata resolvers from the auto-packaging design, and present the result as a set of proposed **packaging rules**: for each family, where the package root sits, how the handle is named, which workflow validates it, and where each metadata field comes from. The admin reviews and adjusts rules in a wizard (or an agent does so through MCP tools), attaches any spreadsheet or LIMS export that already describes the data as a **join table**, runs a dry run, and then executes. Execution builds manifests directly from the catalog, reuses the checksums S3 already stores (CRC64NVME by default on recent uploads, SHA256 where present) and computes the rest with S3 Batch Operations, writes packages in bulk with the smallest high-confidence families first, and is resumable across days. Accepted rules become the bucket's standing policy, so new folders that match a family are packaged automatically and folders that match nothing go to the suggestion queue.

Onboarding is where the bucket is seen whole, which is what makes convention discovery possible; folder-by-folder detection afterwards only has to recognize what onboarding already learned.

# Part 1 — Connecting a bucket today

`bucketAdd` takes a name, title, optional description, icon, tags, relevance score, an existing SNS topic, `scannerParallelShardsDepth`, `skipMetaDataIndexing`, `fileExtensionsToIndex`, `indexContentBytes`, `delayScan`, `browsable`, and `prefixes`. The registry then:

1. finds or creates the SNS topic on the bucket and subscribes the indexer, package-events and EventBridge-routing queues;
2. creates the per-bucket object and package indexes in Elasticsearch, with shard count derived from the shard depth;
3. creates `BulkScannerJob` rows (one per prefix, or many if sharded) that the single-threaded scanner drains over hours to days, one object version per SQS message;
4. registers Glue and Iceberg resources for package tables if `.quilt/` exists, and CloudTrail selectors for access counts;
5. pushes the bucket's content-indexing settings into the indexer Lambda's environment.

The result is a searchable bucket. Nothing surveys the layout, nothing proposes packages, nothing asks where metadata lives, and the admin's description of the bucket is whatever they typed. For a legacy bucket with millions of files and no `.quilt/`, Quilt shows folders and a search box and the work of packaging remains manual.

# Part 2 — Onboarding as a staged ingest

Each stage produces an artifact the next consumes and something visible in the Catalog, so a petabyte bucket shows progress within minutes and first packages within hours.

| Stage | Duration | Produces | Visible |
|---|---|---|---|
| 0 Connect | minutes | permissions verified, S3 Metadata enabled (or S3 Inventory scheduled, or parallel listing started for cross-account buckets), event subscriptions, catalog tables registered | bucket card in "onboarding" state |
| 1 Inventory | hours (S3 Metadata backfill) | `objects` rows for every object; bucket profile: bytes, counts, storage-class mix, version depth, checksum coverage, extension histogram, date range | profile card |
| 2 Survey | minutes to an hour, one analytical pass | `prefix_stats` for every prefix; families with path templates; coverage per family; residual | families table |
| 3 Probe | hours, bounded | recognizer verdicts and resolved metadata for sampled members of each family; discovered identifiers, sidecars, manifests, spreadsheets, prior catalogs; connector suggestions | per-family evidence |
| 4 Propose | interactive | packaging rules: root level, handle template, workflow (existing or drafted), field mappings, examples, counts, warnings; drafted workflow config; join-table bindings; dry-run estimates | onboarding wizard |
| 5 Execute | hours to days, resumable | manifests and pointers written in bulk; checksum jobs; per-family checkpoints; report | progress, first packages |
| 6 Handoff | continuous | accepted rules become `packaging_policies`; journal-delta detection for new roots; residual to suggestions; scheduled re-survey | Packager admin page |

## Stage 0 — Connect

Beyond today's steps: enable Amazon S3 Metadata on the bucket where the account owns it (live inventory plus journal), otherwise create a daily S3 Inventory configuration in Parquet, otherwise fall back to the parallel lister described in the indexing redesign. Record which path was taken because it sets expectations: hours, up to two days, or a crawl. The ES indexes are still created, but the bulk scanner is not started; the search projection is fed from the catalog once it exists (indexing redesign, Phase 2).

## Stage 1 — Inventory

The live inventory table lands in the `objects` catalog. The first query over it yields the **bucket profile**: total bytes and objects, top extensions by count and bytes, storage-class mix (how much is in Glacier tiers and cannot be probed without restore), fraction of objects carrying a checksum and which algorithm, noncurrent-version share, oldest and newest writes, and the top-level prefix breakdown. This card replaces the hand-typed description as the bucket's first summary and drives later decisions: a bucket that is 80 percent Glacier gets a different execution plan than one that is all Standard.

## Stage 2 — Survey

One aggregation pass computes `prefix_stats` for every prefix at every depth: recursive and direct file counts and bytes, an extension histogram, minimum and maximum `last_modified`, child directory count, the set of marker files present directly under it (`ro-crate-metadata.json`, `dataset_description.json`, `RunInfo.xml`, `.zgroup`, `zarr.json`, `README.md`, `datapackage.json`, and the rest of the recognizer vocabulary), and a name-pattern class for the last path segment. For 10^9 objects this is on the order of 10^7 prefixes, a few gigabytes of Parquet, and minutes in Athena or DuckDB.

**Store collapse** runs first: any prefix identified as a Zarr, HDF5 virtual, or DICOM series root absorbs its subtree so that chunk directories never appear as candidates.

**Family detection** then looks at every parent with at least three child directories and computes a shape signature per child: normalized extension histogram, depth profile, order of magnitude of counts and bytes, marker set. Siblings whose signatures agree within a tolerance form a family. A family records its parent, its members, the fraction of the parent's files and bytes it explains, and a **template segment** for the member names: dates in a detected format, UUIDs, hashes, fixed-shape identifiers rendered as a regular expression learned from the members (`[A-Z]{2}\d{5}`), or an enumeration when cardinality is small. Families nest, producing path templates such as `projects/{project}/{run_id}/` or `{instrument}/{user}/{date}_{experiment}/`.

**Root level selection** decides, per family, which template level is the package. Each level gets a dataset-likeness score (marker files present, a mix of data and documentation extensions, writes clustered in time, size within configured bounds) and a grouping-likeness score (its children are themselves dataset-like and homogeneous). The default root is the deepest dataset-like level; the level above is offered as the alternative. A sequencing run folder is a dataset; the `runs/` directory above it is a grouping; a project directory containing runs and analyses is a grouping the admin may still choose as the root if they want one package per project.

The **residual** is everything no family explains: flat directories with thousands of loose files, one-off folders, files at the bucket root. It is reported with its own profile and offered three treatments: package per top-level folder as an archive, cluster by write-time windows, or leave unpackaged.

## Stage 3 — Probe

For each family, sample members stratified by time and size (default 20, fewer for tiny families), and for each sample:

- run the root recognizers over the member's listing and marker files;
- read manifests and sidecars fully, and headers of data files by byte range through the extraction plugins;
- run the metadata resolvers in trust order and record which fields each source produces;
- extract identifier-like tokens (sample ids, run ids, experiment ids) and match them to known grammars and to any configured connector.

Verdicts extrapolate to the family with a confidence that falls with disagreement among samples. The probe also runs bucket-wide discovery for **metadata assets**: spreadsheets and CSV files at shallow depth whose columns match template tokens or identifier grammars (a lab's master sample sheet), LIMS or ELN exports, previous catalogs (`datapackage.json`, `.dvc` files, DataLad datasets, Terra or AnVIL manifests, a `.quilt/` directory from another Quilt stack), and `README` files that describe siblings rather than themselves. Glacier-resident members are marked unprobeable; their family relies on path tokens, sidecars in Standard storage, and join tables.

The agentic recognizer from the auto-packaging design runs here at the family level, not per folder: one call per family with the template, the aggregate profile, and the sampled evidence, asked to name the dataset type, confirm or dispute the root level, and suggest field mappings. Fifty families cost fifty calls.

## Stage 4 — Propose

The wizard shows the profile, then the families ordered by bytes explained, then the residual. Each family becomes a **packaging rule**:

- **Root**: the template level, with the alternative one click away, and three example roots rendered as they would look as packages.
- **Handle**: a template over the path tokens and resolved fields (`{project}/{run_id}`), validated against the workflow's `handle_pattern`, with collision detection across families.
- **Workflow**: an existing one from `.quilt/workflows/config.yml`, or a **drafted** one. The draft is a JSON Schema generated from the fields the resolvers actually found across samples: types from observed values, `enum` for low-cardinality string fields, `dateformat` for detected date formats, `required` for fields present in every sample, descriptions from sidecar documentation. The draft is written to the bucket's workflow config only when the admin accepts it, so onboarding can bootstrap metadata governance for a bucket that never had it.
- **Field mappings**: for each schema field, the source that fills it (path token, sidecar key, manifest property, header aggregate, join-table column, connector lookup, LLM draft marked inferred), the fill rate across samples, and an example value. The admin can reorder sources, add aliases (a sidecar key `Proj` maps to `project`), or pin a constant.
- **Join tables**: the admin points to a spreadsheet or export found by the probe or uploaded now. The system proposes the join key by matching columns to template tokens and identifier grammars, reports match rate against the family's members, and shows unmatched rows and members. One-to-many relationships (a per-sample sheet joined to per-run packages) aggregate to arrays at package level or attach at entry level, chosen per field.
- **Generated files**: whether to add `README.md` (from a template plus resolved fields), `quilt_summarize.json`, and `.quiltignore`.
- **Warnings**: members with recent writes, members in Glacier, members without checksums, members already inside an existing package, handle collisions, required fields with low fill rates.

Global settings: the post-onboarding policy for each family (auto or suggest), the quarantine bucket, checksum strategy, execution order and throttles. A **dry run** reports packages, entries, bytes, checksum work by algorithm, estimated cost, estimated time, and validation failures per family, using the same code path as execution with writes disabled.

Roles: the admin owns the wizard, but each family can be **delegated** to a reviewer (inferred from CloudTrail uploader identity where available, or from bucket tags), who sees only their families. The residual and the post-onboarding policy remain with the admin.

## Stage 5 — Execute

Execution is a resumable job with per-family, per-root checkpoints.

**Manifest construction** reads the catalog, never S3 listings: entries, sizes, and versions come from `objects`; per-entry metadata comes from `object_features` where the workflow's `entries_schema` asks for it; package metadata comes from the rule's mappings; `README.md` and `quilt_summarize.json` are generated if enabled.

**Checksums.** Every entry needs a hash. quilt3 accepts SHA256, sha2-256-chunked, and CRC64NVME. S3 computes and stores CRC64NVME for objects uploaded since December 2024 and stores SHA256 when the uploader requested it; both appear in the S3 Metadata inventory and journal rows and in `GetObjectAttributes`. The strategy, chosen in Stage 4 with the profile's checksum coverage in view:

1. reuse stored checksums where present, preferring the algorithm the bucket's existing packages already use;
2. for Standard-tier objects without a stored checksum, run an S3 Batch Operations checksum job from an inventory manifest (about one dollar per million objects) or the `s3hash` Lambda for small sets, and hold the affected roots until results land;
3. for Glacier-tier objects without a stored checksum, either restore (cost surfaced in the dry run) or defer those roots to a later revision, per family.

Roots whose entries are fully hashed are written first, so the order of appearance is: small high-confidence families, then large families as checksums complete.

**Writing.** The bulk writer validates each package against its workflow, writes the manifest to `.quilt/packages/<hash>` and the timestamp and `latest` pointers to `.quilt/named_packages/<handle>/`, using a dedicated service role scoped to those prefixes, with `dedupe` semantics so a re-run never creates identical revisions. Package events then flow through the existing pointer and manifest indexing into search and the Iceberg package tables. Write rate is throttled against the search ingest's health, because 10^5 packages with 10^4 entries each is 10^9 entry documents under today's one-document-per-entry model; the indexing redesign's compact projection removes that pressure, and until then the throttle protects the cluster.

**Failures.** Validation failures go to the quarantine bucket with the error in `README.md`, as EDP does today, and are listed per family in the report. Transient failures retry with backoff; the job can be paused and resumed; a family can be withdrawn mid-run.

## Stage 6 — Handoff

Accepted rules are stored as `packaging_policies` with their templates, mappings and join-table bindings. From then on, the auto-packaging design's steady-state path takes over: journal deltas or EDP batches surface new roots, template recognizers match them to a family, resolvers fill metadata the same way, and the family's policy decides auto or suggest. New folders that match no family go to the suggestion queue. A scheduled re-survey (weekly by default) recomputes families over the whole catalog and reports **convention drift**: a new family, a family whose shape has changed, or a growing residual. The onboarding report (profile, families, rules, decisions, execution summary) is persisted as the bucket's provenance and is itself a package in the bucket's `.quilt/` namespace.

# Part 3 — Metadata at import time

Historic data has no run-completion event and no uploader session to consult, so the resolver order from the auto-packaging design is weighted toward what is on disk and what the lab already keeps elsewhere:

| Source | Availability on legacy data | What it yields | Notes |
|---|---|---|---|
| Path tokens via family templates | always | project, run, sample, instrument, date, user | The single most reliable source at import; templates are confirmed by the admin |
| Manifests and sidecars | often (pipeline outputs, instrument runs, curated folders) | authors, software, parameters, samples, instrument, dates | Machine-written manifests are trusted; free text becomes description |
| Extracted headers | wherever the extraction plugins cover the format and the object is not in Glacier | organism, reference, instrument, modality, channels, schemas | Aggregated to the root; also fills entry metadata |
| Join tables | wherever the lab has a spreadsheet, LIMS export, or sample sheet | any curated field | Highest practical value; proposed keys, match rates and unmatched rows are shown before use |
| Connectors | when an identifier grammar matches a configured system | fields from the system of record | Rate-limited; best effort during onboarding, complete in steady state |
| Object tags and user metadata | occasionally | whatever the uploader set | Present in inventory rows; free |
| CloudTrail | last 90 days only unless logs are archived | uploader identity | Otherwise "unknown" rather than guessed |
| `last_modified` | always | proxy for acquisition or delivery date | Flagged as a proxy, never presented as the experiment date |
| LLM synthesis | optional | README draft, descriptions, suggested values for empty fields | Always `inferred`; never satisfies a required field under auto |

Every value carries its source in the package's reserved metadata block, and the drafted workflow schema records which sources were expected to fill each field so the steady-state resolver knows where to look.

# Part 4 — Worked examples

## A sequencing core

Profile: 2 PB, 40 million objects, 60 percent `.fastq.gz` by bytes, 30 percent Glacier, CRC64NVME on 45 percent of objects. Survey finds three families explaining 94 percent of bytes: `runs/{run_id}/` (Illumina run folders, markers `RunInfo.xml`, `RunParameters.xml`, `SampleSheet.csv`, `CopyComplete.txt`), `projects/{project}/{sample_id}/` (FASTQ pairs plus QC HTML), and `analysis/{pipeline}/{run_id}/` (nf-core outputs with `pipeline_info/` and `multiqc_report.html`). Probe resolves instrument, flowcell, run date and read structure from `RunInfo.xml`; samples from `SampleSheet.csv`; pipeline name, version and parameters from `pipeline_info/`; organism from BAM headers in analysis outputs. It finds `admin/sample_master.xlsx` whose `SampleID` column matches the `{sample_id}` token at 97 percent and whose `Project` column matches `{project}`. Proposed rules: one package per run at `runs/{run_id}` with handle `runs/{run_id}`; one package per project at `projects/{project}` (the admin moves the root up from `{sample_id}` because a project is the unit scientists ask for), with per-sample metadata from the join table attached at entry level; one package per analysis run. Drafted workflow `sequencing` with required `instrument`, `run_date`, `project`, `organism`, enum `read_type`. Dry run: 1 840 packages, 38 million entries, 22 million checksums to compute for Standard-tier objects (about $22 in Batch Operations), 9 million Glacier objects without checksums deferred. Execution writes analysis packages within the first hour, runs over the first day, projects as checksums complete.

## A microscopy facility

Profile: 800 TB, 300 million objects, dominated by Zarr chunks. Store collapse reduces 300 million objects to 9 000 stores. Survey finds one family, `{instrument}/{user}/{date}_{experiment}/`, whose members contain one or more OME-Zarr plates and a `notes.md`. Root level: the experiment folder (dataset-like: stores plus notes, writes within one day) rather than each store. Probe reads `zarr.json` and `.zattrs` for channels, pixel size, plate layout; `{experiment}` tokens match the ELN's experiment-id grammar, so a connector is suggested. Rule: one package per experiment, handle `{user}/{date}-{experiment}`, workflow drafted with `instrument`, `channels`, `pixel_size_um`, `experiment_id`. Because the extraction plugin reads only store metadata, probing 9 000 stores costs a few thousand small requests.

## A migrated shared drive

Profile: 120 TB, 25 million objects, 3 000 extensions, writes spanning fifteen years, deep and irregular nesting. Survey explains 38 percent of bytes with seven weak families and reports a large residual. The recommendation shown in the wizard is suggest-only: package the seven families, treat the residual per top-level folder as archive packages with generated READMEs summarizing contents, leave the post-onboarding policy at suggest, and schedule a re-survey after owners have had time to reorganize. The onboarding report becomes the map of what is there.

# Part 5 — Changes to the platform

- **API.** `bucketAdd` gains an `onboarding` argument: mode (`survey_only`, `suggest`, `auto_when_confident`), checksum strategy, execution throttles, and initial join tables. New queries expose onboarding status, the profile, families with evidence, proposed rules and the report; mutations accept or edit a rule, bind a join table, start, pause and resume execution, and delegate a family. `delayScan` is superseded by `survey_only`.
- **Catalog.** An Onboarding tab on the bucket admin page with the profile card, the families table, the rule editor, the dry-run view, execution progress, and the report. The Packager admin page shows the resulting policies and the re-survey schedule.
- **MCP.** `survey_bucket(bucket)`, `list_families(bucket)`, `propose_rules(bucket)`, `preview_rule(bucket, family, overrides)`, `apply_rules(bucket, rules, dry_run)`, `onboarding_status(bucket)`. An agent can run the entire flow conversationally, and Qurator can explain a family's evidence in the Catalog.
- **Storage.** `prefix_stats` and `families` as Iceberg tables beside `objects`; `packaging_rules` and the onboarding report in the registry; `packaging_policies` as already defined.
- **Dependencies.** Stage 1 and the survey at billion-object scale need the object catalog (indexing redesign Phase 1); with only S3 Inventory the same survey runs over the Inventory Parquet at daily freshness. Stages 3 and 5 reuse the recognizers, resolvers and policy tables from the auto-packaging design's Phase A. Header probing uses the extraction plugins from indexing Phase 3, with a minimal plugin set (Zarr, OME-TIFF, BAM/VCF headers, Parquet footers, FCS, sidecars) acceptable earlier for probing alone.

# Part 6 — Scale, cost and time at 10^9 objects

| Step | Cost | Time |
|---|---|---|
| S3 Metadata enable and backfill | tens of dollars per month at this size | hours |
| Survey (one Athena pass over a few hundred GB of Parquet, output ~10^7 prefix rows) | under ten dollars | minutes |
| Probe (50 families × 20 samples × a few ranged GETs; 50 agentic calls) | dollars | under an hour |
| Checksums for objects lacking one (Batch Operations) | about one dollar per million objects plus Lambda time | hours to days, paced by the service |
| Manifest writing (10^5 packages, 10^9 entries, hundreds of GB of JSONL) | S3 PUT cost, negligible | hours, throttled by search ingest |
| Search and Iceberg ingestion of entry documents | the dominant load under the current per-entry model | governed by the throttle until the compact projection lands |

Time to first packages is bounded by inventory backfill plus survey plus the smallest family's checksum availability: typically within the first hour after the inventory completes.

# Part 7 — Risks, open questions, verification

## Risks

| Risk | Mitigation |
|---|---|
| Over-packaging legacy clutter | residual is opt-in; families need coverage and confidence thresholds; suggest is the default after onboarding |
| Wrong root level for a family | alternative level shown with examples; delegation to the people who know the data; root level editable later with a re-run that supersedes packages rather than duplicating them |
| Checksum cost or delay on cold data | strategy chosen with coverage in view; deferral per family; Glacier restore only with explicit approval |
| Search cluster overload from entry documents | throttled writer; indexing redesign's compact projection removes the root cause |
| Join-table errors propagate to thousands of packages | match rates and unmatched rows shown before use; per-field provenance; bulk re-resolve creates corrected revisions |
| Sensitive content in sidecars or spreadsheets | field allow-lists; free text excluded from metadata by default; join tables scanned for identifier columns flagged as sensitive |
| Long-running job failure | per-root checkpoints; pause and resume; idempotent writes |
| Cross-account bucket without S3 Metadata | Inventory path with a two-day first report; survey and everything after unchanged |

## Open questions

1. Should packages ever be written with entries lacking a hash, to be filled in a later revision, or is "fully hashed or wait" the rule? Recommendation: wait, and make checksum coverage visible in the profile so the tradeoff is explicit.
2. Handle namespaces for onboarding-created packages: the family template alone, or a bucket-level prefix that marks provenance? Recommendation: template alone, with provenance in metadata; the report lists everything created.
3. Which noncurrent versions, if any, belong in packages created at import? Recommendation: latest only; versions remain in the object catalog.
4. Who approves what: one admin, or delegated reviewers per family? The design supports both; the default should follow the customer's data-ownership model.
5. Should the residual's archive packages exist at all, or does an unexplained folder belong in search only? Likely per customer.

## To verify before build

- S3 Metadata inventory and journal rows carry checksum algorithm and value, and whether CRC64NVME is present on all objects uploaded after the default changed.
- S3 Batch Operations checksum computation: supported algorithms, pacing, and result delivery format.
- `GetObjectAttributes` behavior for objects in Glacier tiers.
- Practical limits on manifest size in the Catalog and Tabulator (the documented 10 000-file guidance) and whether large-family packages should be split.
- Athena or DuckDB cost and time for the prefix aggregation at 10^9 rows on real inventory Parquet.
- ES ingest rate the current cluster tolerates for entry documents, to set the writer's default throttle.
