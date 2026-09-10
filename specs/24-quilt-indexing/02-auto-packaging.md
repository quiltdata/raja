# Auto-Packaging from S3 Folders

**Status:** proposal, no code. **Date:** 2026-09-10. **Companion to:** *Quilt Indexing and Search Redesign*. **Scope:** automatically turning folders in S3 into Quilt packages, using external tools and sources to decide which folders are package roots and to supply their metadata.

# Summary

Quilt already has three pieces of this capability, none of them general. **Event-Driven Packaging (EDP)** batches S3 object events per common prefix at a fixed depth and, after a quiet period or a file count, emits a `package-objects-ready` event that a customer-written Lambda turns into a package. **Packager event rules** auto-package two specific sources, Nextflow runs that emit Workflow Run RO-Crates and AWS HealthOmics run outputs, behind two EventBridge rules an admin can toggle. **Workflows** validate package metadata, entries, and handles against JSON Schema at push time. Root detection is a depth number, metadata is hand-coded per Lambda, existing data is never backfilled, and nothing is proposed for review; it is either published or not.

The proposal replaces the fixed depth and the per-source Lambdas with two plugin registries and a policy layer. **Root recognizers** score candidate folders from evidence: manifests written by pipelines and instruments (`ro-crate-metadata.json`, `dataset_description.json`, `RunInfo.xml`, Zarr roots, `datapackage.json`), structural regularities read from the object catalog (sibling folders with the same shape, homogeneous leaf clusters, write-time cohesion), and, only where those disagree or are absent, an agent that inspects a tree summary. **Metadata resolvers** fill the fields the bucket's workflow schema demands, in a strict order of trust: provenance manifests and system APIs (RO-Crate, HealthOmics, workflow-manager metadata), sidecar files, S3 tags and path tokens, headers extracted by the indexing fleet, external systems through connectors (LIMS, ELN, sequencing platforms, source control), and last an LLM draft that is always labeled as inferred. Every field records its source. A **policy** per bucket or prefix says off, suggest, or auto with a confidence threshold. Auto publishes through the same workflow validation and quarantine path humans use; suggest lands in a review queue in the Catalog and is reachable from the MCP server so agents can propose and create packages too. An identity table maps each detected collection to its package so re-runs revise instead of duplicating, and a package a human has edited is never auto-revised again.

The existing RO-Crate and HealthOmics packagers become the first two recognizer and resolver plugins. The first phase needs nothing from the indexing redesign and can start now; the backfill sweep over legacy data needs the object catalog from its Phase 1, and the agentic pieces need the extraction fleet from its Phase 3.

# Part 1 — What exists today

## 1.1 Three partial mechanisms

| Mechanism | What it does | Where it stops |
|---|---|---|
| Event-Driven Packaging (private preview) | Standalone CloudFormation stack per bucket: Lambda + RDS record S3 events from EventBridge; when `BucketThresholdEventCount` files arrive or `BucketThresholdDuration` seconds pass without a new event under a common prefix of `BucketPrefixDepth` segments, it emits `package-objects-ready {bucket, prefix}` to an event bus | Root is whatever sits at a fixed depth; no content awareness; one stack per bucket; the packaging step is a customer-written Lambda (`quilt3.Package().set_dir(...)`, hard-coded `set_meta`, `push` with a workflow, quarantine bucket plus SNS on validation failure) |
| Packager event rules | Two EventBridge rules, `ro_crate` (Nextflow with nf-prov Workflow Run RO-Crate) and `omics` (AWS HealthOmics run outputs), each wired to a closed-source packaging function; the registry exposes `admin.packager.eventRules` and `toggleEventRule` (`registry/quilt_server/graphql/admin/packager.py`) and the Catalog shows two switches (`PackagerSettings.tsx`) | Exactly two sources, each a bespoke function; enable/disable is the only control; no per-bucket policy, no review, no backfill |
| Workflows | `.quilt/workflows/config.yml` per bucket: named workflows with `metadata_schema`, `entries_schema`, `handle_pattern`, `is_message_required`, `default_workflow`, catalog `package_handle` templates (`<%= username %>/<%= directory %>`), schema `default` values and `dateformat` auto-fill, `successors` for promotion between buckets | Validation only; the schema says what metadata must exist but nothing reads it to decide what to look for |

Two other facts shape the design. The Catalog's Files tab already builds a package from a folder by hand, defaulting the handle to the directory name, so the user-facing concept of "this folder is a package" exists. And the indexing redesign's L2 layer already proposes a **collections** table that groups objects into datasets (Zarr stores, plates, BIDS trees, run folders, package roots) with summaries and embeddings; auto-packaging is the action layer on top of that table.

## 1.2 Gaps

- **Root detection is a number.** Depth 2 is right for `instrument/experiment/` and wrong for `project/assay/run/sample/`, for nested Zarr stores, and for buckets with mixed conventions.
- **Metadata is code.** Each customer writes and maintains a Lambda with literal values; nothing consults the workflow schema, sidecar files, pipeline manifests, or the systems that already know the answer (LIMS, ELN, sequencer software).
- **Only new data.** EDP reacts to events; petabytes of existing unpackaged folders never become packages.
- **Binary outcome.** A candidate is published or dropped; there is no queue where a curator confirms a root, fixes a field, or rejects.
- **No identity.** Nothing remembers that prefix X became package Y, so a second trigger creates a duplicate or a spurious revision, and a manually edited package can be clobbered.
- **Agents cannot ask.** The MCP server can create packages from explicit entries but cannot ask "what would you package under this prefix, and with what metadata?"

# Part 2 — Design

## 2.1 Principles

1. **Provenance beats inference.** A manifest written by the pipeline or instrument outranks a sidecar, which outranks a path token, which outranks an extracted header, which outranks an LLM guess. Every field carries its source.
2. **Roots are scored, not configured.** Evidence from several recognizers is fused; depth is one weak signal among many.
3. **The workflow schema is the target.** Resolvers try to fill exactly the fields the bucket's `metadata_schema` requires; a missing required field blocks auto-publish and becomes a suggestion.
4. **Never surprise a curator.** Default policy is suggest; auto requires an explicit threshold; a package a person has revised is never auto-revised.
5. **Same door as humans.** Auto-created packages go through `push` with the workflow, hit the same validation, and fall into the same quarantine path on failure.
6. **Idempotent and reversible.** One collection maps to one package; re-runs revise; every auto action is attributable and undoable.

## 2.2 Components

```
 triggers ──► candidate folders ──► root recognizers ──► scored roots
                                                              │
   EDP batch events                                           ▼
   external run-complete events            metadata resolvers (ordered by trust)
   catalog sweep (backfill)                                   │
   on-demand (Catalog UI, MCP)                                ▼
                                          policy: off | suggest | auto(threshold)
                                                   │                 │
                                                   ▼                 ▼
                                          suggestions queue     push via workflow
                                          (Catalog, MCP)        → quarantine on failure
                                                   │                 │
                                                   └──────► auto_packages identity table
```

### Triggers

- **EDP batches** stay as the low-latency signal for new data, generalized so the batch key is "any prefix that a recognizer accepts", not a fixed depth. EDP's quiet-period logic is kept as the completeness check.
- **External completion events**: HealthOmics `Run Status Change` (already wired), plus Nextflow, Snakemake, Cromwell, and Step Functions completion events where customers emit them, and LIMS or ELN webhooks that say "experiment closed".
- **Catalog sweep** (needs the object catalog from the indexing redesign): a scheduled job walks `objects` for prefixes not covered by `auto_packages` or by existing `package_entry` rows and feeds them to the recognizers. This is the backfill path for legacy buckets.
- **On demand**: a Catalog action on any folder and an MCP tool, both returning a proposal before anything is written.

### Root recognizers

A registry of plugins, each declaring what evidence it needs and returning zero or more `(prefix, confidence, rationale)` tuples.

| Family | Evidence | Root it declares | Typical confidence |
|---|---|---|---|
| Declared manifests | `ro-crate-metadata.json` (RO-Crate, Workflow Run RO-Crate), `datapackage.json` (Frictionless), Croissant JSON-LD, `dataset_description.json` (BIDS), STAC `collection.json` / `catalog.json`, `quilt_summarize.json`, `README.md` with front matter, `md5sum.txt` / `MANIFEST` | the directory containing the manifest | high |
| Array and image stores | Zarr v2 `.zgroup` + `.zattrs` with `multiscales` or `plate`, Zarr v3 root `zarr.json`, OME-TIFF companion files, HCS plate layouts | the store root, never its chunk subtree | high |
| Pipeline outputs | HealthOmics run output prefix (from the event and `GetRun`), Nextflow `pipeline_info/` + `params.json`, Snakemake `.snakemake/`, Cromwell `cromwell-execution/<workflow>/<uuid>/`, `multiqc_report.html`, Cell Ranger `outs/` | the run directory | high |
| Instrument run folders | Illumina `RunInfo.xml`, `RunParameters.xml`, `SampleSheet.csv`, `CopyComplete.txt`; Oxford Nanopore `final_summary_*.txt`, `sequencing_summary_*.txt`; PacBio `*.subreadset.xml`; mass-spectrometry `.d` and `.raw` folders; microscope export folders with vendor index files | the run or acquisition folder | high |
| Structural (from the catalog) | many sibling directories with the same file-type histogram and depth (`runs/<id>/`), leaf clusters homogeneous in extension, subtree size and count within configured bounds, files written within one time window, identifier-like directory names matching the workflow `handle_pattern` | each sibling | medium |
| Configured templates | per-bucket path templates such as `{project}/{assay}/{run_id}/` in `.quilt/workflows/config.yml` | the template's terminal segment | high when matched |
| Agentic | LLM given a tree summary (depths, counts, sizes, extensions, sample names), sampled headers from the extraction fleet, and the bucket's workflow config; returns roots with rationale | as proposed | medium, capped; used only when the above disagree or find nothing |

**Anti-signals** lower or veto a candidate: ignore prefixes (`scratch/`, `tmp/`, `.quilt/`, configured globs), prefixes already covered by `package_entry` rows or `auto_packages`, evidence of in-progress writes (objects newer than the quiet period, multipart uploads without completion markers, missing `CopyComplete.txt`), and prefixes under a higher-confidence root (a Zarr chunk directory under a store root).

**Fusion.** Scores are combined with recognizer weights; nested candidates resolve to the highest-confidence enclosing root unless a recognizer explicitly declares independence (a BIDS dataset containing per-subject Zarr stores stays one package). Sibling consistency is enforced: if eight of ten `runs/<id>/` siblings are recognized, the other two are proposed at reduced confidence rather than silently skipped. The output is a **candidate**: bucket, prefix, evidence list, fused confidence, proposed handle from the workflow's `package_handle` template or the path template, and the workflow to validate against.

### Metadata resolvers

Resolvers run in trust order and stop filling a field once a higher-trust source has set it. Each returns `{field: (value, source, confidence)}`.

1. **Provenance manifests and system APIs.** Workflow Run RO-Crate gives authors, software and versions, inputs and parameters, start and end times, licenses. HealthOmics `GetRun` and `GetWorkflow` give run id, workflow id and version, parameters, tags, timestamps, output location. Nextflow `params.json` and `pipeline_info/`, Cromwell `metadata.json`, Snakemake metadata, MLflow and DVC files where present. These are machine-written and are trusted as facts.
2. **Sidecar files.** `README.md`, `metadata.json` or `.yaml`, `datapackage.json`, `SampleSheet.csv`, `RunInfo.xml`, `LICENSE`, `CITATION.cff`. Parsed with format-specific readers; free text is kept as description, structured keys map to schema fields by name and by configured aliases.
3. **S3-native.** Object tags and user metadata (present in S3 Metadata inventory rows), and **path tokens** parsed with the bucket's path templates: `{project}/{assay}/{run_id}/` yields three fields without opening a file.
4. **Extracted headers** (from the indexing redesign's `object_features`). Aggregated across the folder: organism and reference genome from BAM and VCF headers, instrument and cytometer from FCS text segments, modality and channels from OME-XML, column schemas from Parquet, read groups and sample names. These fill instrument, assay, species, and file-summary fields.
5. **External connectors** (customer-configured). Keyed by identifiers found in the path or manifests (sample id, run id, experiment id, project code), a connector calls the system of record and returns fields: LIMS and ELN (Benchling, LabArchives, Dotmatics, STARLIMS, in-house), sequencing platforms (Illumina BaseSpace or ICA run metadata), source control and CI (commit, pipeline run URL, container digests), identity (uploader from CloudTrail `PutObject` events). Connector interface: `lookup(identifiers) -> fields with source`; credentials in Secrets Manager; per-connector rate limits.
6. **LLM synthesis** (last, optional). Given everything above plus the tree summary, draft a `README.md` and propose values for still-empty schema fields. Every LLM value is tagged `inferred` and cannot satisfy a required field under auto policy; it can pre-fill the review form under suggest policy. Controlled-vocabulary fields (`enum` in the schema) are never filled by the LLM unless a connector confirms the value.

**Schema-driven targeting.** The resolver reads the workflow's `metadata_schema` and `entries_schema` first, so it knows which fields matter, their types, enums, defaults and `dateformat`, and which entries must exist (a README, a `quilt_summarize.json`). Missing required entries can be generated (README from template plus resolved fields, `quilt_summarize.json` from the file inventory) when policy allows generated files.

**Provenance in the package.** The push message names the recognizer and the trigger. Package metadata carries a reserved block (proposed key `quilt_auto`) with recognizer, confidence, resolver sources per field, connector versions, and the catalog snapshot used, so a reader and a later re-run both know why the package looks the way it does. Entry-level metadata gets the per-file features already extracted, where the workflow's `entries_schema` asks for them.

### Policy and publishing

Per bucket, overridable per prefix glob, stored beside the workflow config:

- **off**: recognizers still run for the sweep report, nothing is written.
- **suggest** (default): candidates land in a suggestions queue with proposed handle, metadata with sources, a preview of entries, and the validation result. A curator approves (optionally editing), snoozes, or dismisses; dismissals are remembered so the same root is not re-proposed until its contents change.
- **auto(threshold)**: candidates at or above the threshold with all required fields resolved from non-inferred sources are pushed with the workflow. Failure of validation follows the existing pattern: push to the quarantine bucket with the error in `README.md` and notify. Anything below threshold falls back to suggest.

Publishing builds the manifest from the catalog rather than re-listing S3 when the catalog exists, sets entry metadata from features, adds generated files if allowed, and pushes with `dedupe` so an unchanged tree never creates an empty revision. The pushing principal is a dedicated service role whose write scope is the target bucket's package prefixes, expressed as the same kind of compiled grant the RAJA work defines.

### Identity and lifecycle

An `auto_packages` table keyed by `(bucket, root_prefix)` records the package handle, the recognizer, the last content fingerprint (from the catalog: count, bytes, max last-modified, or the manifest hash), the policy applied, the state (suggested, published, quarantined, dismissed, human-owned) and timestamps.

- A new trigger on a known root computes the fingerprint; unchanged means no action; changed after the quiet period means a new revision under auto, or a "revision suggested" entry under suggest.
- If the latest revision of the package was not pushed by the service principal, the row flips to **human-owned** and the auto-packager only suggests from then on.
- Deleted roots mark the row and leave the package history intact; the suggestion queue offers a "mark deprecated" revision.
- Renamed prefixes are detected by fingerprint match and offered as a move rather than a new package.

### Interfaces

- **Catalog**: the Packager admin page grows from two switches to a per-bucket policy editor, a recognizer and connector list with health, and a **Suggested packages** view with approve, edit, dismiss. Any folder in the Files tab gets "Propose package", which runs recognizers and resolvers on that prefix and opens the suggestion pre-filled.
- **MCP tools** (extending the agent surface in the indexing redesign): `propose_package(bucket, prefix)` returns the candidate with evidence and resolved metadata and writes nothing; `create_package_from_folder(bucket, prefix, name?, workflow?, metadata_overrides?)` publishes through the same policy and validation; `list_package_suggestions(bucket, state)` and `resolve_suggestion(id, approve|dismiss, edits)` let an agent or a person work the queue. A generated resource documents each bucket's workflow schema so an agent knows which fields it must supply.
- **Events**: every suggestion, publish, quarantine, and dismissal emits an EventBridge event so customers can wire notifications and downstream automation as they do with EDP today.

## 2.3 Data model

| Table | Grain | Key columns |
|---|---|---|
| `package_candidates` | one candidate root per detection run | bucket, prefix, recognizer evidence (JSON), fused confidence, proposed handle, workflow, resolved metadata with sources, validation result, state |
| `auto_packages` | one row per root that has ever been suggested or published | bucket, root_prefix, package handle, recognizer, fingerprint, policy, state, created_by, last_action_at |
| `packaging_policies` | one per bucket or prefix glob | mode, threshold, allow generated files, ignore globs, path templates, connector bindings |
| `connectors` | one per configured external source | type, endpoint, secret ref, identifier patterns it answers, rate limit, health |
| `collections` (from the indexing redesign) | inferred dataset roots | extended with `recognizer`, `confidence`, and a foreign key to `auto_packages` |

# Part 3 — Comparison with today

| Dimension | Today (EDP + event rules + custom Lambda) | Proposed | Net |
|---|---|---|---|
| Root detection | fixed prefix depth per bucket; two bespoke rules for RO-Crate and HealthOmics | scored fusion of manifest, store, pipeline, instrument, structural, template and agentic recognizers | Works across mixed conventions and nested stores; the two existing rules become plugins |
| Metadata | literal values in customer Lambda code | schema-targeted resolution across manifests, sidecars, tags, paths, extracted headers, connectors, LLM; per-field provenance | Fields come from systems of record; every value is explainable |
| Existing data | never packaged | catalog sweep proposes roots across the whole bucket | Legacy petabytes become discoverable as packages |
| Review | none; publish or nothing | suggest queue with edit, approve, dismiss; auto only above threshold | Curators stay in control without writing code |
| Identity | none; duplicates and clobbering possible | `auto_packages` fingerprint and human-owned flag | Re-runs revise; human edits are respected |
| Agents | can push explicit entries only | `propose_package`, `create_package_from_folder`, suggestion tools | Agents can package what they find, under the same policy |
| Deployment | a CloudFormation stack per bucket with its own RDS | one service in the stack; policies and connectors are configuration | Less per-customer infrastructure |
| Validation and failure | workflow push, quarantine, SNS (in the example Lambda) | same, built in | No regression; the pattern becomes the default |

**Tradeoffs accepted.** More configuration surface (policies, templates, connectors) in exchange for no customer code. Recognizer precision will be imperfect on first contact with a new lab's conventions, which is why suggest is the default and why sibling consistency and dismiss memory exist. Connectors are a maintenance commitment; the interface is small and the first two (RO-Crate, HealthOmics) already exist as Lambdas. LLM use is optional, bounded, and never the sole source of a required field.

**Alternatives set aside.** Keeping EDP's depth knob and asking customers for more Lambdas does not scale past a few conventions per bucket. Packaging every leaf directory produces clutter and destroys the dataset notion. Relying only on LLM judgment for roots is expensive at scale and unexplainable; it belongs at the margin.

# Part 4 — Plan

Phases are independent of one another where noted and slot around the indexing redesign's phases.

## Phase A — Generalize the packager (5–7 weeks, no dependency on the indexing redesign)

- Recognizer and resolver plugin interfaces; port the RO-Crate and HealthOmics functions as the first plugins; add declared-manifest, Zarr and instrument-run recognizers; add sidecar, S3-native, path-template and HealthOmics API resolvers.
- Schema-driven targeting against the bucket workflow; generated README and `quilt_summarize.json` where allowed.
- `package_candidates`, `auto_packages`, `packaging_policies` tables in the registry; policy modes; quiet-period completeness check inherited from EDP.
- Catalog: policy editor replacing the two switches; Suggested packages queue; "Propose package" on folders.
- EventBridge events for every action.

Value shipped: any bucket can be set to suggest or auto for common pipeline and instrument layouts with no customer code.

## Phase B — Backfill sweep and structural recognizers (3–4 weeks, after indexing Phase 1)

- Scheduled sweep over the `objects` catalog for uncovered prefixes; anti-signals from `package_entry`.
- Structural recognizers (sibling shape, homogeneity, time cohesion) computed from catalog aggregates rather than listings.
- Sweep report per bucket: coverage, candidates by confidence, unresolvable prefixes.

## Phase C — External connectors (4–6 weeks, parallel with B)

- Connector framework with secrets, rate limits and health; first connectors chosen with design partners (one LIMS or ELN, Illumina BaseSpace or ICA, source control).
- Identifier extraction from paths and manifests; connector bindings in policy.

## Phase D — Agentic recognizer, LLM synthesis, MCP tools (4–5 weeks, after indexing Phase 3)

- Agentic recognizer over tree summaries plus extracted headers; LLM README and field drafting under the inferred label; per-bucket budget.
- MCP tools and the schema resource; Qurator uses them in the Catalog.
- Evaluation harness: labeled buckets with known roots and metadata; precision and recall per recognizer; regression gate for changes.

# Part 5 — Risks, open questions, verification

## Risks

| Risk | Mitigation |
|---|---|
| Wrong roots create clutter or split datasets | suggest by default; sibling consistency; nested-root resolution; dismiss memory; one-click deprecate |
| Inferred metadata treated as fact | per-field source tags; LLM values never satisfy required fields under auto; enums need connector confirmation |
| Packaging an upload still in progress | quiet period; completion markers; fingerprint re-check before push |
| Connector outages or slow systems block publishing | resolvers are best-effort with timeouts; missing connector fields downgrade to suggest rather than fail |
| Auto-revisions churn on busy folders | quiet period per root; minimum interval between auto revisions; `dedupe` on push |
| Cost of LLM calls on large sweeps | agentic recognizer only on disagreement or absence; per-bucket budgets; cached tree summaries |
| Permissions and ownership | dedicated service principal with prefix-scoped write grants; human-owned flag; audit trail entries for every action |
| Sensitive values in manifests or sidecars (patient identifiers) | resolver field allow-lists per bucket; policy option to exclude sidecar free text from package metadata |

## Open questions

1. Handle naming for auto packages: the workflow's `package_handle` template, a bucket-level path template, or a reserved namespace such as `auto/`? Recommendation: template first, namespace only as a fallback.
2. Should suggestions be stored as rows in the registry or as packages in a staging bucket promoted through `successors`? Rows are simpler to review; staging packages are browsable. Recommendation: rows, with a preview rendered from the candidate manifest.
3. How much entry-level metadata to write per file at 10^5 entries per package? Proposal: only fields the `entries_schema` names, plus format and size.
4. Which connector ships first? Depends on design partners; the framework is the deliverable in Phase C.
5. Should the agentic recognizer be allowed to propose roots that no other recognizer sees, or only to arbitrate? Start with arbitrate; widen with evaluation data.

## To verify before build

- HealthOmics run event schema and `GetRun` fields available at completion.
- Workflow Run RO-Crate profile fields emitted by current nf-prov versions.
- Completion markers per instrument family (Illumina `CopyComplete.txt` and equivalents for Nanopore, PacBio, imaging vendors).
- Whether EDP's RDS-backed batching can be replaced by the catalog's journal deltas once S3 Metadata is in place.
- Rate limits and identifier lookups for the first LIMS or ELN connector.
