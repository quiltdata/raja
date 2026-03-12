# RALE CLI — Deconstructed Flow Spec

**Issue:** #46

## Purpose

A command-line walkthrough that makes the RALE authorization-to-retrieval flow visible. The user sees every step that normally happens invisibly inside the Diwan: package selection, USL construction, RAJA token issuance, manifest pinning, RAJEE routing, and object retrieval. If a developer watches this run, they should understand RALE completely.

The CLI is a demo and debugging aid, not a production data path. It deliberately surfaces each intermediate artifact — the USL, the TAJ, the manifest hash, the physical S3 coordinates — that would otherwise be hidden.

---

## Modes

The CLI has two modes, selected at invocation:

| Mode | Flag | Behaviour |
| --- | --- | --- |
| **Auto** | `--auto` (default) | Runs all three phases in sequence without stopping between them. One-shot execution. |
| **Manual** | `--manual` | Pauses after each phase and waits for the user to press Enter before continuing. If a phase's prerequisite is missing (e.g. no TAJ when entering Phase 3), the CLI errors rather than silently backfilling it. |

Manual mode is the default for interactive sessions (TTY detected). Auto mode is the default when stdin is not a TTY (e.g. CI, piped output).

---

## Three Phases

### Phase 1: Setup

Goal: identify the exact object to authorize.

```
RALE CLI — SETUP

  Registry        s3://raja-poc-registry
  RAJEE endpoint  http://localhost:10000

  Available packages:
    [1] test/demo-dataset
    [2] test/sample-reports
    [3] examples/weather-data

  Choose a package: 1

  Package: test/demo-dataset
  Resolving latest hash...

  Hash: a1b2c3d4e5f6...

  Files in test/demo-dataset@a1b2c3d4e5f6:
    [1] data.csv          (1.2 KB)
    [2] README.md         (0.4 KB)
    [3] results.json      (0.3 KB)

  Choose a file: 1

  ──────────────────────────────────────────
  Quilt+ URI (USL):
    quilt+s3://raja-poc-registry#package=test/demo-dataset@a1b2c3d4e5f6&path=data.csv
  ──────────────────────────────────────────
```

**What this does:**

- Reads the configured registry from the environment, config file, or Terraform outputs.
- Calls `quilt3.list_packages()` to enumerate available packages.
- After the user selects a package, resolves the latest immutable hash via `quilt3.Package.browse()`.
- Lists the manifest entries by walking the package.
- Constructs the Quilt+ URI using `raja.quilt_uri.QuiltUri` and displays it.

The Quilt+ URI is the **USL** (Uniform Storage Locator) for the rest of the flow. It reveals nothing about buckets, regions, or keys.

---

### Phase 2: Authorization (RAJA)

Goal: obtain a TAJ from RAJA that is pinned to the selected manifest.

```
RALE CLI — AUTHORIZATION

  Principal:  User::demo-user
  USL:        quilt+s3://raja-poc-registry#package=test/demo-dataset@a1b2c3d4e5f6&path=data.csv

  ── Step 1: Manifest pinning ──────────────────────────────────────────────
  Resolving manifest for hash a1b2c3d4e5f6...
  Pinned manifest:
    entries: 3 objects
    hash:    a1b2c3d4e5f6  (immutable — this scope cannot silently expand)

  ── Step 2: Policy check (AVP) ────────────────────────────────────────────
  Sending to RAJA:
    principal  User::demo-user
    action     quilt:ReadPackage
    resource   quilt+s3://raja-poc-registry#package=test/demo-dataset@a1b2c3d4e5f6

  AVP decision: ALLOW
  Matching policy: permit(principal == User::"demo-user", action == Action::"quilt:ReadPackage", ...);

  ── Step 3: TAJ issuance ──────────────────────────────────────────────────
  RAJA minted a TAJ:

  {
    "sub":   "User::demo-user",       // who this token speaks for
    "aud":   "raja-s3",               // which service will accept it
    "scopes": [                       // compiled permissions (no policy eval at enforcement time)
      "S3Object:raja-poc-registry/test/demo-dataset/:s3:GetObject"
    ],
    "manifest_hash": "a1b2c3d4e5f6",  // pinned — scope cannot drift as dataset evolves
    "iat": 1738800000,                // issued at
    "exp": 1738803600                 // expires at (TTL = 3600s)
  }

  Signed:  HS256 (RAJA, JWT secret from Secrets Manager)
  Token:   eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

  Authorization is complete. The policy decision will not be re-evaluated.
```

**What this does, step by step:**

1. **Manifest pinning** — calls `raja.manifest.resolve_package_map()` for the selected Quilt+ URI. Records the manifest hash. This hash will be embedded in the TAJ, binding the token to an immutable snapshot.

2. **Policy check** — calls `POST /token/package` on the RAJA control-plane server (implemented in `src/raja/server/routers/control_plane.py`). RAJA internally calls AVP with the Cedar policy store. The response includes the decision and the matching policy statement.

3. **TAJ issuance** — on ALLOW, RAJA calls `raja.token.create_token_with_package_grant()` (the same function used by the probe router in `src/raja/server/routers/probe.py`). The CLI decodes the returned JWT with `raja.token.decode_token()` and annotates each claim inline. The signing algorithm and authority are shown as the last line of this step; there is no separate signing step.

---

### Phase 3: Execution (RAJEE)

Goal: use the TAJ to retrieve the actual object through RAJEE.

```
RALE CLI — EXECUTION

  RAJEE endpoint: http://localhost:10000
  TAJ:            eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...  (expires in 3600s)

  ── Step 1: Health check ──────────────────────────────────────────────────
  GET http://localhost:10000/ready  →  200 OK
  RAJEE is reachable.

  ── Step 2: Route and enforce ─────────────────────────────────────────────
  HEAD http://localhost:10000/  [Authorization: Bearer <TAJ>]

  x-raja-signature-valid:  true         // TAJ signature verified against JWKS
  x-raja-scope-match:      true         // requested scope ⊆ granted scopes
  x-raja-manifest-member:  true         // data.csv ∈ manifest a1b2c3d4e5f6
  x-raja-physical-bucket:  raja-poc-registry
  x-raja-physical-key:     test/demo-dataset/data.csv
  Status: 200 OK

  ── Step 3: Object retrieval ──────────────────────────────────────────────
  GET http://localhost:10000/ [Authorization: Bearer <TAJ>]

  Bytes received: 1,234
  ──────────────────────────────────────────
  col1,col2,col3
  alpha,1,true
  beta,2,false
  gamma,3,true
  ──────────────────────────────────────────

  Flow complete.
  The TAJ is valid for 3598 more seconds.
  Subsequent requests to this manifest reuse the cached token (Router Lambda only).
```

**What this does, step by step:**

1. **Health check** — calls `GET /probe/rajee/health?endpoint=<url>` on the RAJA server (implemented in `src/raja/server/routers/probe.py`). Surfaces a reachability error before attempting the full probe.

2. **Route and enforce** — calls `POST /probe/rajee` with the principal, USL, and RAJEE endpoint. The server sends a `HEAD` to RAJEE and returns the `x-raja-*` diagnostic response headers in a single round trip. Each header maps to one enforcement step inside the Envoy Lua filter:
   - Signature verification (RAJEE checks against the JWKS endpoint)
   - Scope subset check (no policy evaluation — pure membership test)
   - Manifest membership check (requested key ∈ pinned manifest entries)
   - Physical address resolution (logical → bucket + key)

3. **Object retrieval** — issues the actual `GET` (or `s3:GetObject` via configured AWS endpoint) using the presigned SigV4 request that RAJEE forwarded to S3, and streams the bytes to the terminal.

**Manual mode:** if Phase 2 has not been run and the CLI is in manual mode, Phase 3 errors with *"No TAJ available — run authorization phase first."* It does not silently re-run Phase 2.

---

## Error States

Each phase has a specific failure mode the CLI should surface clearly:

| Phase | Failure | CLI output |
| --- | --- | --- |
| Setup | Unknown package | "Package not found in registry" |
| Setup | Hash resolution failure | "Cannot resolve latest hash — check registry access" |
| Authorization | AVP DENY | "DENY — no Cedar policy permits this principal + action + resource" |
| Authorization | RAJA unreachable | "RAJA server not reachable at <url>" |
| Execution | RAJEE unreachable | "RAJEE not reachable at <url> — run health check" |
| Execution | Signature invalid | "TAJ signature invalid — token may have been tampered with or key rotated" |
| Execution | Manifest member not found | "data.csv not a member of manifest a1b2c3d4e5f6 — token may be stale" |
| Execution | Scope mismatch | "Requested scope not covered by TAJ grants" |

All errors halt the current phase with a clear message and suggest the corrective action. The system never silently proceeds past a failed step.

---

## Configuration

The CLI resolves values in this priority order:

1. **Environment variables** (highest priority)
2. **Local config file** (`~/.config/raja/cli.toml`)
3. **Terraform outputs** — `terraform output -json` read from `infra/terraform/` relative to the detected repo root, or the path in `RAJA_TF_DIR`

| Variable | TF output key | Default | Description |
| --- | --- | --- | --- |
| `RAJA_SERVER_URL` | `api_url` | `http://localhost:8000` | RAJA control-plane endpoint |
| `RAJA_REGISTRY` | `rajee_registry_bucket_name` ¹ | *(required)* | Default Quilt registry (e.g. `s3://raja-poc-registry`) |
| `RAJEE_ENDPOINT` | `rajee_endpoint` | `http://localhost:10000` | RAJEE proxy endpoint |
| `RAJA_ADMIN_KEY` | — | *(required)* | Admin key for `Authorization: Bearer` on probe endpoints |
| `RAJA_PRINCIPAL` | — | `User::demo-user` | Default principal for CLI demo runs |
| `RAJA_TF_DIR` | — | `infra/terraform` | Path to Terraform working directory |

¹ `rajee_registry_bucket_name` is a bare bucket name; the CLI prepends `s3://` to form the registry URI.

When reading from Terraform outputs the CLI runs `terraform output -json` once per session and caches the result. If the Terraform directory does not exist or `terraform` is not on `PATH`, this step is silently skipped and the defaults apply.

---

## Implementation Notes

The CLI is a thin orchestration layer over code that already exists. No new library logic is needed:

| CLI step | Existing code |
| --- | --- |
| List packages | `quilt3.list_packages()` |
| Resolve manifest | `raja.manifest.resolve_package_map()` |
| Parse/display USL | `raja.quilt_uri.parse_quilt_uri()`, `QuiltUri.normalized()` |
| Request TAJ | `POST /token/package` → `create_token_with_package_grant()` in `control_plane.py` |
| Decode + annotate TAJ | `raja.token.decode_token()` |
| RAJEE health check | `GET /probe/rajee/health` in `probe.py` |
| RAJEE live probe | `POST /probe/rajee` in `probe.py` |
| Scope → grant display | `raja.rajee.grants.convert_scopes_to_grants()` |

The CLI entry point is `src/raja/cli.py`, registered as `rale` in `pyproject.toml` `[project.scripts]`. It can also be run directly with `python -m raja.cli`.

---

## What This Is Not

- Not a production data path. Applications use the Diwan, which does this invisibly.
- Not a policy authoring tool. Use the admin UI for Cedar policy management.
- Not a full boto3 integration test. Use `./poe test-integration` for that.
- Not a substitute for RAJEE. The CLI calls the RAJA probe endpoint; it does not replace Envoy.
