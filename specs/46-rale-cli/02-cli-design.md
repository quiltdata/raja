# RALE CLI — Design Document

**Issue:** #46

---

## Purpose

Demo and debugging tool for the RALE flow. Not a production data path.

---

## Technology

| Concern | Choice | Rationale |
| --- | --- | --- |
| Language | Python 3.14+ | Same as the library; no new runtime |
| CLI framework | `click` | Already used in the repo; supports TTY detection |
| Config | `toml` (stdlib `tomllib`) | Human-editable; no extra dependency |
| Output | `rich` | Phase headers, inline annotations, and formatted token display without manual ANSI |
| HTTP client | `httpx` | Already a transitive dependency; supports sync and async |

---

## Architecture

The CLI is a thin orchestration layer. It owns no business logic — every step delegates to existing library code or RAJA server endpoints.

```
rale (cli.py)
 ├── config.py       — resolves env vars, toml, terraform outputs
 ├── phase1.py       — setup: package list → file selection → USL
 ├── phase2.py       — authorization: manifest pin → policy check → TAJ
 └── phase3.py       — execution: health check → probe → object retrieval
```

State flows forward through a single `SessionState` object (principal, USL, TAJ). Phases are functions; they do not call each other. The runner in `cli.py` calls them in sequence and passes state along.

---

## Key Design Decisions

**Thin layer, no new logic.** Every substantive operation maps to an already-tested function or HTTP endpoint. The CLI adds only sequencing, display, and error surfacing.

**TTY-driven mode selection.** Manual mode when stdin is a TTY; auto mode otherwise. This means the same binary works for interactive demos and CI pipelines without flags.

**Forward-only state.** Phase 3 does not re-run Phase 2 if the TAJ is missing in manual mode. It errors. This enforces the flow contract and prevents silent re-authorization.

**Eager error surfacing.** Each phase checks its preconditions before doing any work and halts with a clear message on failure. No silent fallbacks.

**Single config resolution pass.** Terraform outputs are read once at startup and cached in `SessionState`. Subsequent phases read from the cache.

**Entry point as `rale`.** Registered in `pyproject.toml` `[project.scripts]`; can also be invoked as `python -m raja.cli`.

---

## What Is Deliberately Out of Scope

- Policy authoring (use the admin UI)
- Production data retrieval (use the Diwan)
- Full boto3 integration testing (use `./poe test-integration`)
- Replacing or wrapping Envoy/RAJEE
