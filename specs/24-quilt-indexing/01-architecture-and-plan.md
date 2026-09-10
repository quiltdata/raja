# Quilt Indexing and Search Redesign

**Status:** proposal, no code. **Date:** 2026-09-10. **Scope:** replacing the bulk scanner, indexer Lambdas, and Elasticsearch-centric search with an architecture that scales to buckets of 10^9 objects and petabytes of multi-modal scientific data, optimized for AI agents performing scientific analysis.

# Summary

Today Quilt learns what is in a bucket by crawling it one list page at a time, turning every object version into an SQS message, and having a Lambda `HEAD` and partially `GET` each object before writing one Elasticsearch document per version. That design is coupled at every stage to object count: crawl time, message volume, S3 requests, Lambda invocations, and cluster heap all grow linearly with the number of versions in the bucket. Content extraction is an extension allow-list that reads the first 100 KB of text formats and skips the imaging, array, and sequencing formats that make up most scientific bytes. Reindexing deletes the index and replays everything. Agents reach all of this through the same `query_string` syntax built for humans.

The proposal inverts the stack. **An Iceberg catalog becomes the source of truth**, populated from Amazon S3 Metadata's inventory and journal tables instead of a crawler, so enumeration of a billion objects costs tens of dollars a month and no Quilt code. **Extraction becomes a scheduled, format-aware job** that reads Parquet footers, Zarr and OME metadata, BAM and VCF headers, FCS text segments, and DICOM tags by byte range, writing structured features back to the catalog under explicit per-bucket budgets. **Collections** (packages, Zarr stores, plates, run folders) get generated summaries and embeddings, so semantic search operates over millions of datasets rather than billions of files. **Search is derived**: Elasticsearch holds a bounded projection rebuilt from the catalog with alias swaps, and can later be swapped for an S3-native engine. **Agents get SQL first** over documented semantic views, hybrid search second, and byte-range sampling third, through a reshaped MCP tool set.

The live SNS/SQS event path, per-bucket isolation, package-level typed metadata facets, the existing Iceberg package tables, and the MCP server are all kept. The bulk scanner, its Postgres job table, prefix sharding, inline content reads, and delete-and-replay reindexing are removed.

Six phases over roughly six to eight months. Phases 1 and 2 (catalog plus scanner retirement) remove the scaling ceiling on their own and should go first.
# Part 1 — How indexing and search work today

## 1.1 Pipeline

1. **Trigger.** Bucket add (GraphQL `bucketAdd`), admin reindex (`POST /api/admin/reindex/<bucket>`), deploy-time index migration, or a pointer backfill creates `BulkScannerJob` rows in the registry's Postgres table. Sharding by prefix depth (`scanner_parallel_shards_depth`) is computed synchronously inside the HTTP request by walking `CommonPrefixes`.
2. **Crawl.** `flask bucket_scanner <IndexerQueue>` is a single-threaded loop. It checks out one job (`SELECT … FOR UPDATE SKIP LOCKED`, newest first), calls `list_object_versions` 1000 keys at a time for at most 20 pages, and persists `next_key_marker`/`next_version_id_marker` so the job resumes later. Versions and delete markers are merged into one stream.
3. **Enqueue.** Every object version becomes one synthetic SNS-style S3 event (`OBJECTS_PER_MESSAGE = 1`) and is sent in batches of 10 to the **same SQS queue** that receives live S3 notifications. SQS send failures are logged and lost.
4. **Index (object path).** The `indexer` Lambda unwraps records, `HEAD`s every object, fetches object tags, and, for allow-listed extensions, `GET`s the first N bytes (default 100 KB, per-bucket override) or the whole object for notebooks, PDFs, Parquet, Excel and PowerPoint. It writes one Elasticsearch document per **object version** (`_id = key:versionId`) into the per-bucket index via `helpers.bulk`.
5. **Index (package path).** `.quilt/named_packages/*` pointers are indexed inline as `ptr` child docs. `.quilt/packages/<hash>` manifests are forwarded to `manifest_indexer`, which writes a `mnfst` parent doc plus one `entry` child doc per file, flattens `user_meta` into typed facet records, and upserts `was_packaged` onto object docs. Bulk bodies are staged as NDJSON in an S3 bucket and drained by `es_ingest`.
6. **Package tables.** A separate `iceberg` Lambda MERGEs pointer and manifest events into per-bucket Iceberg tables (`package_revision`, `package_tag`, `package_manifest`, `package_entry`) via Athena. Tabulator resolves packages from these tables.
7. **Search.** GraphQL `searchObjects`/`searchPackages` translate to ES `query_string` plus structured filters, per-bucket alias, `terminate_after=10 000` per shard. The MCP server's `search_packages`/`search_objects` tools pass the same syntax through.

## 1.2 Where it strains at billions of objects

| Concern | Today | Consequence at 10^9 objects / PB scale |
|---|---|---|
| Enumeration | Sequential `ListObjectVersions`, 1000 keys/call, one worker per job | ~10^6 list calls per bucket, serialized; prefix sharding is manual and done inside an HTTP request that can time out on wide buckets |
| Fan-out granularity | 1 SQS message and ≥1 `HEAD` + `GetObjectTagging` per object version | 10^9 messages, 2–3 × 10^9 S3 requests, 10^9 Lambda record handles just to learn what a listing already told us |
| Index unit | One ES doc per object version, content stored inline | Index size scales with versions, not logical files; hot JVM heap is the binding constraint (documented in enterprise scripts README) |
| Reindex | Delete indexes → recreate → replay everything; no blue/green alias swap | Search is empty for the duration of a reindex; `auto_create_index=false` is needed to stop the live queue from racing the rebuild |
| Job model | LIFO checkout, retry counter on `ClientError` only, no lease/heartbeat, no completion accounting | Old jobs starve; crashed workers leave partial work; `last_indexed` is wrong for sharded scans; exhausted jobs linger |
| Correctness gaps | No `DeleteObjects`, lifecycle expiry, or tag-change events; `HEAD` failures skipped silently | Index drifts from S3 with no reconciliation loop other than a full rescan |
| Content extraction | Extension allow-list; first 100 KB of text; whole-object reads for Parquet/PDF/notebooks/Excel; Parquet skipped when larger than Lambda memory | Scientific formats (TIFF/OME, Zarr, HDF5, BAM/CRAM, VCF, DICOM, mzML, CZI) get filename and size only; the data that matters to agents is opaque |
| Two catalogs | ES has objects + packages; Iceberg has packages only | Agents must know which system to ask; no SQL over object metadata |
| Query surface | ES `query_string` behind GraphQL and MCP | Good for humans typing; poor for agents that want typed predicates, aggregations, joins with manifests, and paging over millions of hits |
| Cost shape | ES cluster must be sized for the whole corpus and stay hot | Cost grows linearly with corpus, not with query volume; storage is duplicated (S3 + ES) |

## 1.3 What works and should be kept

- **Event-driven freshness.** SNS → SQS → Lambda gives seconds-level updates for live writes. Keep the event path; change what it feeds.
- **Packages as first-class search entities** with typed `user_meta` facets. This is Quilt's differentiator and maps directly onto agent needs.
- **Per-bucket isolation** (indexes, IAM read roles, config). Preserve as a tenancy and permissions boundary.
- **Iceberg package tables.** Already the right storage substrate; the redesign extends this pattern to objects.
- **MCP server as the agent surface.** The tools exist; they need a richer backend.

# Part 2 — Proposed architecture: a lakehouse catalog with a thin search tier

## 2.1 Design thesis

Stop treating the search engine as the index. Make an **Iceberg catalog of every object and package** the source of truth, populated from S3's own inventory rather than from crawling, and derive everything else (full-text, vectors, UI listings) from it. Extraction becomes a **prioritized, format-aware, budgeted** batch over the catalog instead of an inline side effect of every S3 event. Agents get **SQL first**, hybrid search second, and byte-range access third.

This mirrors what every system that survives at 10^9 files does (Rucio, Terra Data Repository, Azul, Starfish, S3 Metadata itself): a relational or columnar catalog underneath, a denormalized search index on top, never the other way round.

## 2.2 Layers

```
                 S3 buckets (customer-owned, versioned, PB scale)
                        │                          │
      ┌─────────────────┴──────────┐     ┌─────────┴──────────────┐
      │ L0  Enumeration            │     │ L0' Live change stream  │
      │ S3 Metadata live-inventory │     │ S3 → EventBridge/SQS    │
      │ tables (Iceberg, managed)  │     │ (seconds; existing path)│
      │ Fallback: S3 Inventory     │     └─────────┬──────────────┘
      │ importer / parallel lister │               │
      └─────────────────┬──────────┘               │
                        ▼                          ▼
      ┌────────────────────────────────────────────────────────────┐
      │ L1  Object catalog  (Iceberg, per bucket)                    │
      │ objects: bucket,key,version_id,etag,size,last_modified,      │
      │          storage_class,tags,user_metadata,is_delete_marker,  │
      │          is_latest, format_guess, extraction_state           │
      │ + existing package_revision / package_tag / package_manifest │
      │   / package_entry tables                                     │
      └────────────────────────────┬───────────────────────────────┘
                                   ▼
      ┌────────────────────────────────────────────────────────────┐
      │ L2  Extraction fleet (format plugins, ranged GETs, budgets)  │
      │ object_features: format, schema/columns, dims, channels,     │
      │   axes, row counts, header JSON (VARIANT), sample_text,      │
      │   checksum, extractor_version, cost                          │
      │ collections: inferred datasets (Zarr stores, plates, BIDS,   │
      │   run folders, package roots) with summaries + embeddings    │
      └────────────────────────────┬───────────────────────────────┘
                                   ▼
      ┌──────────────────────────┐   ┌──────────────────────────────┐
      │ L3a Analytical query     │   │ L3b Discovery index           │
      │ Athena / DuckDB / Trino  │   │ BM25 + vectors over packages, │
      │ over L1+L2 via semantic  │   │ collections, documents, and a │
      │ views (files, datasets,  │   │ compact object projection.    │
      │ images, alignments, …)   │   │ OpenSearch today; S3-native   │
      │                          │   │ engine later. Rebuildable.    │
      └────────────┬─────────────┘   └───────────────┬──────────────┘
                   └──────────────┬──────────────────┘
                                  ▼
      ┌────────────────────────────────────────────────────────────┐
      │ L4  Agent + human surface: MCP server, GraphQL, Catalog UI   │
      │ search → describe → sql → sample/read → package/lineage      │
      └────────────────────────────────────────────────────────────┘
```

### L0 — Enumeration without crawling

- **Primary: Amazon S3 Metadata.** Enabling it on a bucket gives two Iceberg tables in an S3 table bucket: a **journal table** (one row per mutation, including deletes and tag/metadata changes, minutes-fresh) and a **live inventory table** (current state of every object, backfilled for existing objects). Both are queryable with Athena, Spark, DuckDB and PyIceberg with no infrastructure of ours. This replaces `ListObjectVersions` paging, the `BulkScannerJob` table, prefix sharding, and the 20-page/30-second job loop.
- **Fallback: inventory importer.** For buckets where S3 Metadata cannot be enabled (cross-account buckets whose owner will not configure it, regions without support, non-versioned legacy edge cases), run a daily **S3 Inventory** report (Parquet) into the same L1 schema. Last resort: a parallel lister built on Step Functions distributed map over prefixes, writing Parquet directly. Never one message per object.
- **Live stream stays.** SNS/EventBridge → SQS continues to deliver seconds-level events. It now feeds two consumers: a small "hot path" that upserts into the discovery index for immediacy, and a MERGE into L1 that the journal table later confirms. The journal table is the reconciliation authority; the live stream is a latency optimization, not a correctness dependency.

### L1 — Object catalog

One Iceberg table per bucket (matching the existing per-bucket Iceberg package tables), partitioned by top-level prefix and bucketed by key hash. Columns come straight from inventory plus three we own: `format_guess` (extension + magic-byte rules), `extraction_state` (none/queued/done/failed/skipped-by-policy), and `collection_id`. Rows are cheap: 10^9 objects is a few hundred GB of Parquet, which Athena scans for cents with partition and column pruning.

Deletes, versions, lifecycle expiry, tag changes and `DeleteObjects` all appear in the journal table, closing the correctness gaps the event-only design has today.

### L2 — Extraction as a scheduled, budgeted job

Replace "read the first 100 KB of allow-listed text files inside the S3 event handler" with a **plugin registry keyed by format**, each plugin declaring what byte ranges it needs and what it emits:

| Format family | Ranged read | Emits |
|---|---|---|
| Parquet / Arrow / ORC | 8-byte tail then footer (KB–MB) | schema, row count, row-group stats, column names |
| Zarr / OME-Zarr (v2, v3, NGFF 0.5) | `.zmetadata` or root `zarr.json` | shape, dtype, chunking, axes, channels, multiscale levels, plate/well layout |
| OME-TIFF / TIFF / CZI / ND2 / LIF | header + first IFD or segment directory | OME-XML: dims, channels, pixel size, instrument |
| HDF5 / NetCDF / h5ad | virtual reference scan (kerchunk / VirtualiZarr) once | dataset tree, shapes, attrs; reference file stored for zero-cost later reads |
| BAM / CRAM / VCF / BCF | first BGZF blocks (+ `.bai` sidecar) | SAM/VCF header: reference, samples, read groups, program chain |
| FASTQ | first 64 KB–1 MB | read length, quality encoding, instrument, estimated record count |
| FCS | 58-byte header then TEXT segment | parameters, cytometer, event count |
| mzML / mzXML | first 100 KB + tail index | instrument, run metadata, spectrum count |
| DICOM | tags before pixel data | modality, patient/study/series (de-identified as policy dictates) |
| CSV / TSV / Excel / JSON | first 64 KB–1 MB | sniffed schema, header row, sample rows |
| Notebooks / Markdown / PDF / PPTX | whole file (small) | title, headings, text for full-text and chunk embeddings |

Every plugin returns a structured `header` (stored as JSON/VARIANT), a short `sample_text` for BM25 (bounded, kilobytes not the 100 KB blobs stored today), and a `summary` string used for embeddings at the collection level. Plugins are versioned; a version bump marks affected rows `extraction_state=queued` rather than forcing a bucket rescan.

Scheduling reads the catalog, not a queue of events: `SELECT … WHERE extraction_state='queued' ORDER BY priority`. Priority favors recently modified objects, objects referenced by packages, and formats with high information yield; policy can cap bytes-per-bucket-per-day. Execution options, in order of preference: Step Functions distributed map over Parquet manifests (10 000-way concurrency, item batching, retries, no home-grown job table), or S3 Batch Operations invoking the same Lambda from an inventory manifest, or a Fargate fleet for heavy Java-based readers (Bio-Formats). The per-event Lambda still exists for the hot path but is now thin.

**Collections.** A second pass groups objects into datasets: a Zarr store root, an OME plate, a BIDS subject tree, a sequencing run folder, and, above all, a Quilt package. Each collection gets a generated natural-language summary (format mix, sizes, key metadata, README text) and one embedding. Collections number 10^5–10^7 even when objects number 10^9, which keeps vector search cheap and semantically meaningful: agents look for *datasets*, not individual chunk files.

### L3a — SQL as the primary agent interface

Publish a small set of documented **semantic views** over L1+L2 in the Glue catalog: `files`, `datasets`, `packages`, `images`, `alignments`, `arrays`, `tables`, `documents`. Column descriptions live in the Iceberg schema so text-to-SQL prompts can be assembled from the catalog itself. Athena is the default engine (already deployed for Tabulator and the Iceberg Lambda); DuckDB in the MCP server process serves sub-second interactive queries against recent Parquet snapshots for the catalog UI.

### L3b — Discovery index, derived and bounded

Keep OpenSearch for now, but change what goes into it:

- Package, pointer and manifest docs as today (these work).
- Collection docs (new) with summary text and vectors.
- Document chunks for notebooks, PDFs, Markdown, protocols.
- A **compact object projection**: key tokens, format, extracted column and channel names, sizes, dates. No raw content blobs; no one-doc-per-version unless a bucket opts in.

Because the index is a projection of Iceberg tables, it is rebuilt by a batch job into a fresh index and swapped in with an alias, ending the delete-and-replay reindex outage. The same job can target an S3-native engine (Quickwit or OpenSearch UltraWarm for text, LanceDB, turbopuffer or S3 Vectors for vectors) when the OpenSearch bill justifies moving. Vectors are quantized (int8 or binary) and the top-k is reranked with a cross-encoder inside the MCP server.

### L4 — Agent surface

Reshape the MCP tools around how catalog agents behave (the DataHub and Snowflake servers are the reference shape):

1. `search(query, filters, kind=package|dataset|object|document)`: hybrid BM25 + vector with typed filters, returns ids and short cards, not payloads.
2. `describe(id)`: full extracted header, schema, collection membership, packages containing it, sample rows.
3. `sql(query)`: read-only over the semantic views, with row and byte caps and the view documentation returned alongside results.
4. `sample(id, byte_range | rows | region)`: ranged reads, Arrow slices, Zarr chunk fetch.
5. `lineage(id)`: packages, revisions, workflows, source events.

Plus a **Search Syntax / schema resource** generated from the catalog so the agent never guesses field names. Permissions are enforced by bucket-level row filters (the existing per-bucket boundary) rather than post-hoc `HEAD` calls.

## 2.3 Data model summary

| Table | Grain | Source | Rows at scale |
|---|---|---|---|
| `objects` | object version | S3 Metadata / Inventory / events | 10^9 |
| `object_features` | object (latest etag) | extraction fleet | 10^8–10^9, sparse |
| `collections` | inferred dataset or package root | grouping pass | 10^5–10^7 |
| `collection_embeddings` | collection | embedding job | 10^5–10^7 |
| `package_*` (existing) | revision / entry | Iceberg Lambda | 10^6–10^8 |
| `document_chunks` | chunk | extraction fleet | 10^7 |

# Part 3 — Comparison with the current design

## 3.1 Side by side

| Dimension | Today | Proposed | Net |
|---|---|---|---|
| Source of truth | Elasticsearch documents | Iceberg tables (objects, features, collections, packages) | Rebuildable, SQL-queryable, versioned snapshots; ES becomes a cache |
| Enumeration | Registry crawls `ListObjectVersions` 1000 keys at a time, one worker per job, prefix sharding by hand | S3 Metadata live inventory (backfill measured in hours, then updates within the hour) plus journal deltas; S3 Inventory fallback | Removes the scanner service, the job table, and the sharding heuristic; billions of objects enumerated with zero Quilt code paths |
| Per-object cost to learn metadata | ≥1 SQS message, 1 `HEAD`, 1 `GetObjectTagging`, 1 Lambda record | 0 (already in the inventory row, including tags and user metadata) | At 10^9 objects: roughly 2–3 × 10^9 S3 requests and 10^9 SQS messages avoided |
| Content extraction | Inline in the event handler, extension allow-list, 100 KB text prefix, whole-file reads for Parquet/PDF/notebooks | Scheduled, format-aware plugins reading headers and footers by byte range; explicit budgets and priorities | Covers imaging, arrays, alignments, flow, mass spec; bounded bytes per object; retryable without rescanning |
| Freshness | Seconds via SNS/SQS for live writes; full rescans take days | Seconds via the same live stream for the hot path; hours for catalog reconciliation; deep features lag by policy | Same interactive freshness, plus a guaranteed catch-up path that today does not exist |
| Deletes, lifecycle, `DeleteObjects`, tag edits | Missed or partially handled | Journal table records all of them | Index converges to S3 without manual reindex |
| Reindex | Delete index, recreate, replay bucket; search unavailable meanwhile | Batch rebuild from Iceberg into a fresh index, alias swap | No outage; rebuild time depends on projection size not corpus size |
| Versions | One ES doc per object version forever | Versions live in Iceberg; the discovery index carries latest by default | Discovery index shrinks by the version multiplier |
| Search for humans | ES `query_string` across text fields, per-bucket facets | Unchanged UI contract at first; later backed by projection + SQL views | No regression; adds dataset-level results |
| Search for agents | Same `query_string` through MCP | `search → describe → sql → sample → lineage`, hybrid retrieval, typed filters, documented views | Agents can ask quantitative and join questions instead of guessing field syntax |
| Vector / semantic search | None | Collection-level and document-chunk embeddings, quantized, S3-backed | 10^5–10^7 vectors instead of 10^9; affordable |
| Permissions | Per-bucket index; optional post-hoc `HEAD` filtering ("Secure Search") | Per-bucket tables and row filters at query time; ACL columns in the projection | Consistent with RAJA-style compiled grants; no per-hit S3 calls |
| Ops burden | ES cluster sizing, JVM heap, shard counts fixed at index creation, scanner process, job table | Managed Iceberg (S3 Tables) and Athena; extraction fleet is stateless; ES sized for the projection | Fewer bespoke moving parts, more managed ones |
| Cost shape (order of magnitude, 10^9 objects) | ES cluster large enough to hold every version doc with content, always on | S3 Metadata tens of $/month; Iceberg storage a few hundred GB; Athena per-scan; one-time extraction backfill in the low thousands of $ (Lambda or Fargate); ES for a projection of 10^7–10^8 docs | Cost tracks extracted information and query volume, not raw object count |

## 3.2 Tradeoffs the proposal accepts

- **Two consistency horizons instead of one.** Catalog rows converge within about an hour; the hot path shows live writes in seconds. Users and agents must be told which they are looking at. Today's design promises seconds and silently misses classes of change; the new one promises eventual convergence and delivers on it.
- **Latency for cold analytical queries.** Athena answers in seconds, not the tens of milliseconds ES gives. Interactive UI paths (typeahead, bucket browsing) stay on the projection or on DuckDB over cached snapshots. SQL is for questions that ES cannot answer at all today.
- **AWS coupling.** S3 Metadata is AWS-only and must be enabled by the bucket owner, with tables landing in the owner's account. Quilt is already AWS-native, but cross-account buckets need the inventory-importer fallback and a permission story for reading the owner's table bucket.
- **More formats means more code.** A plugin registry with a dozen readers (some Java-based, like Bio-Formats) is a real maintenance surface. It replaces a single 100 KB text sniffer, and it is the entire reason agents will find scientific data.
- **Cost moves from fixed to variable.** Extraction budgets and Athena scans are knobs to tune; a runaway agent issuing unbounded SQL can spend money. Caps per tool call are a hard requirement, not a nicety.
- **Migration effort.** The registry, Lambdas, GraphQL resolvers, and MCP tools all change. The plan below sequences this so each phase ships value and the ES path keeps working until the last step.

## 3.3 Alternatives considered and set aside

- **Scale the current design** (more scanner workers, bigger ES). Linear cost in object count; no path to structured or semantic search; reindex outages persist.
- **OpenSearch Serverless or OR1 as the sole store.** Solves heap pressure, not the fundamental one-doc-per-version, content-inline model; at 10^9 docs the bill is thousands per month before vectors.
- **Bedrock Knowledge Bases or Kendra.** Hard ceilings in the millions of documents; no scientific format parsing.
- **Vector-first (embed every object).** 10^9 embeddings cost thousands to tens of thousands of dollars to produce and terabytes to store, and file-level chunks of binary data embed poorly. Embedding collections and documents captures what agents actually search for.
- **A pure DuckDB or Parquet full-text approach with no search engine.** Attractive for cost, but full-text indexes over billions of rows must be rebuilt as snapshots; keep a real inverted index for the bounded projection.

# Part 4 — Plan

Each phase is independently shippable and leaves the existing ES path working. Estimates assume a small team and are relative, not commitments.

## Phase 0 — Prove the assumptions (2–3 weeks)

1. Enable S3 Metadata on a large internal test bucket. Measure backfill time, journal latency, whether noncurrent versions appear in the live inventory table, and whether tags and user metadata are populated.
2. Benchmark header extraction per format on real customer-like data: bytes read, wall time, failure modes. Targets from research: under 2 MB and under 3 GETs for Parquet, Zarr, OME-TIFF, BAM, VCF, FCS, DICOM.
3. Cost model spreadsheet: 10^7, 10^8, 10^9 objects; extraction via Lambda vs Fargate; Athena scan volumes for the planned views; ES projection size.
4. Decide the engine for the discovery index for the next two years (stay on OpenSearch vs move to an S3-native engine). Default: stay, shrink.

Exit criteria: measured numbers replace the unverified ones flagged in the research; a go/no-go on S3 Metadata as the primary enumerator.

## Phase 1 — Object catalog in Iceberg (4–6 weeks)

- Per-bucket `objects` Iceberg table populated from S3 Metadata (or the inventory importer). Reuse the existing per-bucket Iceberg conventions from the package tables.
- Terraform/CloudFormation for the metadata configuration, table bucket access, Glue registration.
- Athena views: `files`, `packages` (join to existing package tables). Expose a read-only `sql` MCP tool with row and byte caps and a schema resource.
- Reconciliation job: nightly diff of catalog vs. ES doc counts per bucket, reported in the admin UI.

Value shipped: any bucket, of any size, becomes queryable by agents within hours of connection, with no scanner run.

## Phase 2 — Retire the bulk scanner (3–4 weeks)

- The registry's "reindex" and "add bucket" actions stop creating `BulkScannerJob` rows. Instead they enqueue a **catalog-driven backfill**: a Step Functions distributed map (with item batching) over the `objects` table that emits the same synthetic events the indexer already understands, in batches of many objects per message, into a **separate** queue from live events.
- Keep the ES indexer Lambda unchanged so search behavior is identical; only the producer changes.
- Delete the scanner service, job table, sharding heuristic and `QUILT_BULK_SCANNER_MAX_PAGES` once the new path has run on every connected bucket.

Value shipped: reindex no longer depends on a single-threaded crawler; full-bucket backfill time drops from days to hours; live events are no longer starved by backfills.

## Phase 3 — Extraction framework (6–8 weeks)

- Plugin interface: `sniff(bytes) → format`, `ranges(size, format) → [byte ranges]`, `extract(chunks) → header, sample_text, summary`. Port existing readers (Parquet, FCS, notebooks, PDF, Excel, PPTX, CSV) first; add Zarr/OME-Zarr, OME-TIFF, HDF5/h5ad via VirtualiZarr, BAM/CRAM/VCF, FASTQ, DICOM, mzML.
- `object_features` table written by the extraction fleet; `extraction_state` and priorities on `objects`; per-bucket byte and dollar budgets in bucket config (replacing `fileExtensionsToIndex` and `indexContentBytes`).
- Scheduler: hourly query for queued rows → batched Lambda (or Fargate for heavy Java readers). Backfill via S3 Batch Operations or distributed map.
- ES object documents now sourced from `objects` + `object_features`, not from inline reads. Content blobs shrink to `sample_text`.

Value shipped: scientific formats become searchable by schema, dims, channels, samples, instruments.

## Phase 4 — Collections, summaries, embeddings (4–6 weeks)

- Grouping rules: package roots, Zarr stores, OME plates, BIDS trees, run folders, README-bearing prefixes.
- Summary generation (template-first, LLM-assisted where README or manifest text exists) and one embedding per collection; document-chunk embeddings for notebooks, PDFs, Markdown.
- Vector store: quantized index in the discovery engine, with S3 Vectors as the bulk tier if volume warrants.
- New `dataset` result kind in GraphQL and the Catalog UI.

## Phase 5 — Agent surface (3–4 weeks, overlaps Phase 4)

- MCP tools: `search`, `describe`, `sql`, `sample`, `lineage`; a generated schema/syntax resource; per-call caps; permission filters by bucket grant.
- Hybrid ranking (BM25 + vector + reranker on top-50) inside the MCP server; typed filters map to Iceberg predicates and ES filters.
- Qurator and the Catalog search page use the same tools.

## Phase 6 — ES as a derived cache (2–3 weeks)

- Projection rebuild job: Iceberg → fresh index → alias swap. Remove `auto_create_index` gymnastics and the delete-and-replay reindex.
- Evaluate moving the projection to an S3-native engine if cost or ops warrant; the rebuild job makes this a config change.

## Rough total

About 6–8 months of elapsed time for a small team with Phases 4 and 5 overlapping; Phases 1 and 2 alone remove the scalability ceiling and should be prioritized.

# Part 5 — Risks, open questions, verification list

## Risks

| Risk | Mitigation |
|---|---|
| S3 Metadata cannot be enabled on a customer bucket (cross-account owner declines, unsupported region, org policy) | Inventory importer is a first-class path, not an afterthought; parallel lister as last resort. Both write the same `objects` schema |
| Live inventory table omits noncurrent versions or delete markers | Verify in Phase 0; if so, versions come from the journal table going forward and from a one-time `ListObjectVersions` backfill for history |
| Extraction plugins are wrong or slow on real data (proprietary microscopy formats especially) | Budgets per bucket; `extraction_state=failed` with reason; formats opt in per deployment; Bio-Formats readers run on Fargate, not Lambda |
| Athena cost from agent SQL | Hard caps on bytes scanned and rows returned per call; views pre-filter by bucket grants; DuckDB snapshot for interactive paths |
| Two freshness horizons confuse users | Surface "cataloged at" and "features extracted at" timestamps on every hit; hot path keeps seconds-level visibility for live writes |
| Migration regressions in search behavior | Phase 2 keeps the indexer Lambda intact; golden-query test suite over a fixed bucket run against old and new paths |
| Embedding quality for scientific collections | Embed generated summaries built from structured fields plus README text; evaluate retrieval against a hand-labeled set before enabling in the UI |
| De-identification and sensitive headers (DICOM, sample IDs) | Plugins declare sensitivity per field; extraction policy per bucket decides what lands in the catalog |

## Open questions for the team

1. Which discovery engine for the next two years: stay on OpenSearch with a shrunken index, or move to an S3-native engine once the projection is rebuildable? Recommendation: stay through Phase 5, decide in Phase 6 with real numbers.
2. Do we keep one ES document per object version for buckets that require version-level search, or make that an opt-in per bucket?
3. What is the policy default for extraction budgets on a newly connected 10^9-object bucket: metadata only until an admin opens the tap, or a fixed daily byte budget?
4. Should collection inference run inside Quilt or be offered as a workflow hook customers can extend (their own grouping rules)?
5. How do compiled RAJA grants map onto Iceberg row filters and the projection's ACL columns, so that the same grant governs SQL, search, and byte access?

## Numbers to verify before relying on them

Flagged by the research agents as unverified because vendor documentation was unreachable from the research environment:

- S3 Metadata journal table latency ("minutes"), exact column list, and whether live inventory is free below 10^9 objects.
- Live inventory coverage of noncurrent versions and delete markers.
- Step Functions distributed map maximum concurrency (10 000) and Lambda default burst limits.
- S3 Vectors index size limits and query throughput.
- OpenSearch Serverless per-OCU capacity.
- FCS byte layout details and mzML index tail behavior (verify with the parsers on real files).

