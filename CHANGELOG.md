# Changelog
<!-- markdownlint-disable MD024 -->

All notable changes to the RAJA project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.2.0] - 2026-03-18

### Added

- **`--package N` CLI flag**: Pass `--package <number>` to pre-select a package by index (1-based), skipping the interactive prompt entirely.
- **RESTful principal membership API**: Replaced the flat `/principals` write routes with a symmetric principal-centric API:
  - `GET /principals/projects/{project_id}` — members of a specific project
  - `GET /principals/{principal}/projects` — all projects a principal belongs to
  - `POST /principals/{principal}/projects/{project_id}` — grant access (no request body)
  - `DELETE /principals/{principal}/projects/{project_id}` — revoke access

### Changed

- **Dynamic DataZone projects**: `DataZoneConfig` now stores an arbitrary `projects: dict[str, ProjectConfig]` map instead of three hardcoded `owner/users/guests` fields. Configuration is read from a single `DATAZONE_PROJECTS` JSON env var.
- **`sagemaker_gaps.py` and seed scripts updated**: Lambda env sync now writes `DATAZONE_PROJECTS` (one JSON blob) instead of nine individual `DATAZONE_*_PROJECT_ID / ENVIRONMENT_ID / PROJECT_LABEL` variables. `.env` outputs use `datazone_projects` and `datazone_project_ids`.
- **`seed_users.py`: `RAJA_GUESTS` overflow removed**: The separate guest seeding path (`RAJA_GUESTS` env var, overflow into the last project) has been removed; all principals are seeded via the unified `RAJA_USERS` path.
- **Terraform: ignore `owning_project_identifier` drift** on the `QuiltPackage` asset type to prevent unwanted plan noise after domain recreation.

### Removed

- **Scope derivation dead code**: All scope-assignment logic removed from the control plane — `_scopes_for_project()`, `RAJA_PROJECT_SCOPES` env var, and the `scopes` field on `PrincipalRequest`. Authorization is DataZone subscriptions; tokens are issued with empty scopes.
- **`project_id_for_scopes()`**: Hardcoded scope-to-project mapping removed from `datazone/service.py`.
- **Hardcoded `owner/users/guests` project env vars** (`DATAZONE_OWNER_PROJECT_ID`, etc.): Consolidated into `DATAZONE_PROJECTS`.

## [1.1.0] - 2026-03-18

### Added

- **IAM-authenticated RALE flow**: RALE now performs full IAM authentication end-to-end, resolving the caller principal via `STS GetCallerIdentity` instead of accepting an arbitrary `--principal` flag.
- **RALE deny error detail**: The RALE authorizer now surfaces structured error detail on DENY decisions, making it easier to diagnose authorization failures from the CLI output.
- **Integration tests for RALE**: New integration tests cover the IAM-authenticated RALE flow and deny-path error reporting.
- **Environment creation in `sagemaker_gaps.py`**: `_ensure_environments()` creates `raja-registry` DataZone environments for projects on the custom blueprint; projects on the All-capabilities profile are skipped with a clear actionable message.
- **Terraform: raja-registry blueprint configuration**: New `aws_datazone_environment_blueprint_configuration` resource pins the custom blueprint for environment provisioning.

### Changed

- **Python 3.14**: Project repinned to Python 3.14 (`.python-version`, `pyproject.toml`).
- **RALE CLI: `--principal` flag removed**: Principal is always resolved from STS; the flag was misleading and caused confusing 403s when plain usernames bypassed STS membership lookup.
- **`sagemaker_gaps.py`: Lambda env sync includes project IDs**: `DATAZONE_OWNER_PROJECT_ID`, `DATAZONE_USERS_PROJECT_ID`, and `DATAZONE_GUESTS_PROJECT_ID` are now synced to Lambda alongside environment IDs, fixing DENY errors after domain recreation rotates project IDs.
- **Terraform: ignore console-managed drift**: Domain role/name/description changes and project description drift are now ignored to prevent unintended `ForceNew` replacements of console-created resources.
- **Admin UI**: Lambda and S3 operational links are grouped with nested lists; Logs links are inlined. About card no longer has a constrained `max-width`. Reading list expanded to 9 chronologically sorted posts.
- **Symmetric seed topology**: Seed script produces a symmetric user topology aligned with RALE access audit requirements.

### Removed

- **Cedar/AVP remnants**: All remaining Cedar/AVP artifacts removed — `src/raja/cedar/` empty package, `docs/cedar-*.md`, `docs/cedar-admin.html`, `tools/cedar-validate/` Rust binary, dead failure-test categories 2.1–2.7, stale Terraform descriptions, and all CLAUDE.md Cedar references.
- **`_TIER_SCOPES` / `_TIER_PROJECT_ENV`** removed from `scripts/seed_users.py` (Cedar-era constants, no longer used).
- **`.rale-seed-state.json` tracking**: File is now gitignored; environment-specific AWS account IDs and ARNs should not be committed.

### Fixed

- **DataZone Tooling blueprint**: Documented six V2 gotchas (policy grants, env config IDs, Tooling prerequisite, immutable domain S3, IAM users, `regionalParameters` keys) that were causing `Invalid S3 path provided null` failures.

## [1.0.0] - 2026-03-16

### Added

- **Subscription management in the Admin UI**: Admins can now review package subscription requests and approve or revoke access directly from the control plane.
- **Automatic access provisioning for new principals**: Adding a principal now provisions the corresponding DataZone subscription grant automatically instead of requiring a separate manual step.
- **SageMaker Studio domain support**: Deployments now provision and manage the Studio domain needed for DataZone-backed user workflows.

### Changed

- **Admin UI redesign**: The admin experience has been reorganized into a clearer two-column layout with dedicated sections for domain structure, test data, live execution, and operational links.
- **RALE principal resolution**: RALE can now infer the caller principal from AWS STS when it is not configured explicitly, reducing setup friction for real AWS users.

### Fixed

- **Health reporting for RALE services**: Authorizer and router health checks now report correctly, preventing false unhealthy status in the Admin UI.
- **Subscription request visibility**: Pending DataZone subscription requests are now detected and displayed correctly in the control plane.

## [0.9.0] - 2026-03-12

### Added

- **`raja.datazone` module** (`src/raja/datazone/service.py`): New DataZone service replacing Amazon Verified Permissions as the authorization backend
  - `DataZoneService` class encapsulating all DataZone API interactions
  - Policy compilation, principal scope management, and authorization checks via DataZone
- **Unit tests** (`tests/unit/test_datazone_service.py`): 416-line comprehensive test suite for the DataZone service module
- **RALE select tests** (`tests/unit/test_rale_select.py`): Unit coverage for RALE package selection logic
- **SageMaker/DataZone migration specs** (`specs/48-use-sagemaker/`): Design documents covering the migration from AVP to DataZone
  - `01-sm-ticket.md` — migration requirements and ticket breakdown
  - `02-sm-setup.md` — DataZone setup and configuration guide

### Changed

- **Authorization backend**: Replaced Amazon Verified Permissions (AVP) with Amazon DataZone throughout the stack
  - Terraform infrastructure (`infra/terraform/`) reconfigured for DataZone resources; AVP policy store removed
  - Control plane (`src/raja/server/routers/control_plane.py`) now uses `DataZoneService` instead of AVP client
  - RALE authorizer Lambda (`lambda_handlers/rale_authorizer/handler.py`) uses DataZone for authorization decisions
  - Server dependencies (`src/raja/server/dependencies.py`) inject DataZone service instead of AVP client
- **Terraform outputs** (`infra/terraform/outputs.tf`): Updated to export DataZone resource identifiers; AVP policy store ARN removed
- **Terraform variables** (`infra/terraform/variables.tf`): Replaced AVP-specific variables with DataZone domain/project configuration
- **Seed scripts**: `scripts/seed_packages.py` and `scripts/seed_test_data.py` wired to DataZone for principal and scope management
- **`scripts/show_outputs.py`**: Updated to display DataZone-specific Terraform outputs
- **`pyproject.toml`**: Added `datazone` boto3-stubs type stub; removed AVP stubs
- **Integration tests**: Updated for AVP → DataZone migration; failure mode and control plane tests reflect new authorization model

### Removed

- **Cedar/AVP infrastructure**: All Cedar policy machinery removed as DataZone supersedes it
  - `src/raja/cedar/` package (`__init__.py`, `entities.py`, `parser.py`, `schema.py`) deleted
  - `src/raja/compiler.py` — Cedar-to-scope compiler removed
  - `policies/` — Cedar policy files (`schema.cedar`, `rajee_integration_test.cedar`, `rajee_test_policy.cedar`, `rale_demo_user.cedar`, `rale_package_grant_test.cedar`) removed
  - `scripts/load_policies.py` — Cedar policy loader (288 lines) removed
  - `infra/terraform/scripts/apply_avp_schema.py` — AVP schema application script removed
  - `scripts/test_all.sh` — superseded by poe tasks
- **Unit tests for removed Cedar modules**: `test_cedar_parser.py`, `test_cedar_schema.py`, `test_cedar_schema_parser.py`, `test_cedar_schema_validation.py`, `test_compiler.py`, `test_compiler_forbid.py`, `test_compiler_templates.py` all removed
- **Hypothesis compilation tests** (`tests/hypothesis/test_compilation.py`): Removed with Cedar compiler

### Fixed

- **Live RALE tests**: Pass `rale_authorizer_url` and `rale_router_url` explicitly in `test_rale_cli_live.py` to avoid missing-output errors
- **RALE authorizer**: Pin manifest hash in authorizer path and fix TAJ claims display in admin UI

## [0.8.0] - 2026-03-12

### Added

- **RALE CLI** (`rale` command): New end-to-end demo runner for the RALE authorization flow
  - Multi-phase orchestration: authorize → select → fetch, with auto and manual (step-through) modes
  - Config resolution from `.env` files, Terraform outputs, and CLI flags (`--server-url`, `--registry`, `--rajee-endpoint`, `--admin-key`, `--principal`, `--tf-dir`)
  - Rich terminal console output with phase status indicators
  - `rale = "raja.cli:main"` entry point registered in `pyproject.toml`
- **`raja.rale` package**: Modular implementation of the RALE CLI phases
  - `config.py` — config resolution and validation with `ConfigOverrides` and Terraform output loading
  - `authorize.py` — calls control-plane to issue a TAJ token for the demo principal
  - `select.py` — resolves a Quilt package and selects a logical S3 path
  - `fetch.py` — fetches the physical S3 object through the RAJEE proxy using the TAJ token
  - `runner.py` — orchestrates all phases in sequence
  - `console.py` — `Console` wrapper for styled Rich output
  - `state.py` — `SessionState` dataclass carrying phase results between steps
- **`rale_demo_user` Cedar policy**: New policy granting the demo principal access for CLI walkthroughs
- **Integration test** (`test_rale_cli_live.py`): Live end-to-end test of the full RALE CLI flow against a deployed stack
- **Unit tests** (`test_rale_cli.py`): Offline unit coverage for CLI config, phase logic, and failure modes
- **New dependencies**: `click`, `httpx`, `rich`, `python-dotenv`, `quilt3`, `awscrt` added to package runtime deps

### Changed

- **RALE CLI hardening**: All endpoint URLs (`server_url`, `rajee_endpoint`, `registry`) are now required; the CLI fails fast with a clear error rather than falling back to defaults
- **`python-dotenv` support**: Config resolution now loads `.env` files automatically before resolving environment variables
- **Integration test skips → failures**: Tests that previously skipped when endpoints were absent now fail, ensuring CI catches misconfigured environments
- **Terraform outputs**: Added `rale_authorizer_url` and `rale_router_url` output variables to `infra/terraform/`

### Fixed

- **mypy errors**: Resolved type errors in `console.py`, `select.py`, and `manifest.py` (`import-untyped` → correct ignore comment)

## [0.7.0] - 2026-03-10

### Added

- **Admin key authentication**: Control-plane endpoints (`/compile`, `/token`, `/policies`, `/principals`) now require an `X-Admin-Key` header; unauthenticated requests return 401.
- **Live-tour admin UI**: Interactive walkthrough of the full RAJA pipeline (compile → issue → enforce) with per-step probe diagnostics and status indicators.
- **Secret-rotation revocation flow**: Rotating the JWT signing secret now atomically revokes all tokens issued under the previous key; unit and integration tests cover the full revocation lifecycle.
- **`show-outputs` script**: `scripts/show_outputs.py` pretty-prints the current Terraform outputs for quick stack inspection without opening raw JSON.
- **`rajee-registry` S3 bucket**: New Terraform-managed S3 bucket for the RAJEE package registry; `scripts/seed_packages.py` seeds test package data into it.

### Changed

- **Admin UI**: Redesigned as a logical-data discovery journey — Overview explains how the system works, backstory moved to About page; restyled with the W3C Swiss stylesheet; static assets and API calls now use relative paths so the UI works correctly behind an API Gateway stage prefix.
- **S3 harness removed**: The synthetic S3-harness endpoints have been replaced with the real RAJA compile → token → enforce pipeline end-to-end.

### Fixed

- **Health check hang**: Fixed a hang in the `/health` endpoint that could block the admin UI on startup.
- **API Gateway stage prefix**: Static asset URLs and `fetch()` calls are now relative, fixing broken resources when deployed under a non-root stage path.

## [0.6.1] - 2026-03-09

### Changed

- **Terraform outputs**: Removed `legacy_cdk_outputs` CDK compatibility shim; deploy now writes a flat `infra/tf-outputs.json` with native Terraform output names. All consumers (integration helpers, compiler, load_policies, build-envoy-image) updated to use snake_case keys directly.
- **RALE authorizer**: Support un-pinned USLs — authorizer now resolves the latest manifest hash from DynamoDB when no hash is present in the quilt URI.

## [0.6.0] - 2026-02-27

### Added

- **RALE (Resource Access Logical Endpoint)**: New routing mode for logical S3 access via TAJ tokens
  - `TAJToken` model for Translation Access JWT with logical bucket/key and quilt URI claims
  - `create_taj_token()` and `validate_taj_token()` functions for TAJ lifecycle management
  - RALE Authorizer Lambda (`lambda_handlers/rale_authorizer/`) — issues TAJ tokens given a principal and quilt URI
  - RALE Router Lambda (`lambda_handlers/rale_router/`) — validates TAJ, resolves logical key via manifest cache, and fetches physical S3 object
- **Terraform infrastructure**: Full RAJA stack deployable via Terraform (replaces CDK for primary deployment)
  - `infra/terraform/main.tf` — unified stack: API Gateway, Lambda functions, DynamoDB tables, AVP policy store, IAM, ECS/Envoy cluster
  - `infra/terraform/variables.tf` — parameterized configuration for VPC, region, bucket prefixes, and RALE URLs
  - `infra/terraform/outputs.tf` — exports for RALE Lambda ARNs/URLs, DynamoDB table names, Envoy endpoint
  - `infra/terraform/versions.tf` — provider version pinning
  - `infra/terraform/scripts/apply_avp_schema.py` — applies Cedar schema to AVP policy store post-deploy
  - `infra/terraform/.terraform.lock.hcl` — provider lockfile committed for reproducible builds
- **Envoy RALE routing mode**: Lua filter extended with RALE-aware request routing
  - Detects `x-rale-taj` header to route to RALE Router Lambda
  - Detects `x-raja-principal` (no TAJ) to route to RALE Authorizer Lambda for token bootstrap
  - Falls back to existing RAJEE JWT+scope path when RALE environment variables are absent
  - `RALE_AUTHORIZER_URL` and `RALE_ROUTER_URL` environment variables gate RALE mode
- **Integration tests**: End-to-end RALE test suite (`tests/integration/test_rale_end_to_end.py`)
  - Bootstrap flow: principal → TAJ issuance via RALE Authorizer
  - Data request flow: TAJ → manifest validation → physical S3 fetch via RALE Router
  - Shared test helpers (`tests/integration/helpers.py`) for token construction and endpoint resolution
- **Documentation**:
  - `docs/rale-internal-ops.md` — operator guide covering request flow, runtime routing conditions, and DynamoDB table usage
  - `specs/5-rale/01-diwan-stories.md` — user stories for the Diwan client runtime and logical S3 namespace
  - `specs/5-rale/02-rale-terraform-impl.md` — detailed Terraform implementation specification
- **`quilt3` dependency**: Added to Lambda layer (`infra/raja_poc/layers/raja/requirements.txt`) for manifest resolution

### Changed

- **Infrastructure**: Terraform is now the primary deployment path; CDK remains for legacy use
- **Specs**: Reorganized `specs/` directory; MVP specs moved to `.github/1-mvp/`
- **README**: Updated references from CDK to Terraform deployment workflow
- **.gitignore**: Added `terraform.tfvars` (user-specific secrets) and Terraform state files

### Fixed

- **Terraform policy loader**: Corrected policy loading script to properly seed AVP from Cedar files
- **Lambda wheel builds**: Lambda packages now built for `linux/amd64` regardless of host architecture

## [0.5.0] - 2026-01-22

### Added

- **Manifest-based authorization**: Package grant and translation grant support for Quilt packages
  - New `Package` entity type in Cedar schema with registry, packageName, and hash attributes
  - `quilt:ReadPackage` action for package-level authorization
  - `PackageToken` model for immutable package grants (`quilt_uri` + `mode`)
  - `PackageMapToken` model for logical-to-physical path translation grants
  - `PackageAccessRequest` model for S3 access requests in package context
  - `PackageMap` class for resolving package manifests to physical S3 locations
- **Package grant enforcement**: Content-based authorization anchored to immutable package manifests
  - `enforce_package_grant()` - validates package membership via manifest resolution
  - `enforce_translation_grant()` - validates logical path translation to physical S3 locations
  - `enforce_with_routing()` - routes enforcement based on token claim structure (scopes vs packages)
  - Package name wildcard matching (e.g., `my/pkg/*` matches `my/pkg/subdir`)
  - Package scope parsing and validation (`Package:pkg@hash:read`)
- **Token creation functions**: Factory functions for package-based tokens
  - `create_token_with_package_grant()` - issue package grant tokens with Quilt URIs
  - `create_token_with_package_map()` - issue translation grant tokens with logical paths
  - `validate_package_token()` - validate and decode package grant tokens
  - `validate_package_map_token()` - validate and decode translation grant tokens
- **Quilt URI utilities**: Parse and validate Quilt package URIs (`src/raja/quilt_uri.py`)
  - URI parsing with registry, package name, and hash extraction
  - Package name wildcard matching for hierarchical authorization
  - URI validation with comprehensive error messages
- **Package map utilities**: S3 path parsing and package manifest resolution (`src/raja/package_map.py`)
  - Parse S3 paths into bucket/key components
  - Resolve package manifests from registry to physical locations
- **Lambda handler**: Package resolver Lambda for manifest resolution (`lambda_handlers/package_resolver/`)
- **Integration tests**: Comprehensive demonstrations of manifest-based authorization
  - `test_rajee_package_grant.py` - 4 tests for package grant enforcement (allow/deny member files, write operations)
  - `test_rajee_translation_grant.py` - 6 tests for translation grant enforcement (mapped/unmapped paths, multi-region, write operations)
  - `test_package_map.py` - integration test for package map resolution
- **Documentation**: Extensive design and implementation documentation
  - `docs/rajee-manifest.md` - admin-facing guide for manifest-based authorization
  - `specs/4-manifest/01-package-grant.md` - package grant design (903 lines)
  - `specs/4-manifest/02-package-map.md` - package map design (52 lines)
  - `specs/4-manifest/03-package-gaps.md` - analysis of gaps and edge cases (336 lines)
  - `specs/4-manifest/04-package-hardening.md` - security hardening considerations (441 lines)
  - `specs/4-manifest/05-package-more.md` - advanced features and extensions (746 lines)
  - `specs/4-manifest/06-demo-coverage.md` - demonstration coverage analysis (371 lines)
- **Unit tests**: Comprehensive unit test coverage for new modules
  - `test_manifest.py` - 64 lines of manifest parsing and validation tests
  - `test_package_map.py` - 22 lines of package map utility tests
  - `test_quilt_uri.py` - 55 lines of Quilt URI parsing and validation tests
  - Expanded `test_enforcer.py` with 306+ new lines for package grant enforcement
  - Expanded `test_token.py` with 168+ new lines for package token validation
  - Expanded `test_compiler.py` with 23+ new lines for package scope compilation
  - Expanded `test_control_plane_router.py` with 91+ new lines for package grant API endpoints

### Changed

- **Cedar parser**: Removed legacy Cedar statement parsing (`parse_cedar_to_statements()`)
  - Parser now focuses on policy extraction and validation
  - Simplified parser interface with fewer internal parsing steps
- **Compiler**: Enhanced to support package scopes in policy compilation
  - Added package scope extraction from Cedar policies
  - Support for `Package` entity types in policy analysis
- **Enforcer**: Extended with package-aware authorization logic
  - Package scope matching with wildcard support
  - Package action validation (read-only enforcement)
  - Routing logic to dispatch between scope-based and package-based enforcement
- **Token operations**: Extended with package grant validation and creation
  - Token validation now handles multiple claim structures (scopes, quilt_uri, logical paths)
  - Comprehensive error handling for malformed package tokens
- **Control plane API**: Enhanced with package grant token issuance endpoints
  - Extended `/token` endpoint to support `grant_type=package` and `grant_type=translation`
  - API now accepts `quilt_uri`, `logical_bucket`, `logical_key`, and `logical_s3_path` parameters
  - Expanded API response models to include package grant tokens
- **Public API**: Expanded exports to include package grant functionality
  - 15+ new exports in `src/raja/__init__.py` for package grants
  - All package-related models, functions, and utilities now publicly accessible
- **Dependencies**: Added `pyproject.toml` dev dependencies for manifest testing

### Fixed

- **Type checking**: Fixed type errors in package grant enforcement logic
- **Code formatting**: Applied ruff formatting across all new modules

## [0.4.4] - 2026-01-21

### Added

- **Cedar CLI integration**: Native Rust-based Cedar policy compilation with Python fallback
  - Rust tool `cedar-validate` for policy parsing and validation
  - Cedar CLI installation in CI workflows (Linux + macOS)
  - Lua + LuaRocks installation for Envoy testing
- **Hierarchical S3 authorization**: Bucket-level and object-level scope enforcement
  - Template expansion for exact bucket validation
  - Prefix-based authorization with wildcard support
  - Scope validation utilities with comprehensive tests
- **Failure mode testing**: Comprehensive test harness for validation gaps
  - 40+ failure mode test runners for admin UI
  - Property-based testing with hypothesis
  - Integration tests for failure scenarios
- **Test utilities**: Shared token builder and S3 client helpers
  - Centralized token generation utilities (`tests/shared/token_builder.py`)
  - S3 client helpers for integration tests (`tests/shared/s3_client.py`)
- **Documentation**:
  - Cedar/AVP authorization model documentation
  - PostgreSQL schema for RAJEE
  - RAJEE manifest and integration architecture
  - Comprehensive failure mode analysis and fixes
  - Schema validation specifications (`specs/3-schema/`)
  - Cedar integration README

### Changed

- **Test coverage**: Improved from 82% to 90% (#22)
- **Scope enforcement**: Enhanced hierarchical S3 scope matching in Envoy Lua filters
- **Token validation**: Stricter JWT validation and security checks
- **Compiler**: Support for forbid policies and template expansion
- **Control plane**: Enhanced `/compile` and `/token` endpoints with audit logging
- **CI workflow**: Now runs full test suite including Lua tests (not just unit tests)
- **Documentation structure**: Moved integration proof to specs directory

### Fixed

- **Lua tests**: Fixed 13 failing tests with proper security validations and error handling
- **CI**: Install luarocks on macOS and fail loudly on missing test tools
- **Schema validation**: Added exception chaining (B904 linting fix)
- **Import organization**: Applied ruff import cleanup across codebase
- **Test suite**: Fixed failures in integration and unit tests with improved security validation

### Security

- **Enforcer hardening**: Fail-closed enforcement with explicit deny for malformed requests
- **Token validation**: Enhanced JWT validation with issuer and expiration checks
- **Scope validation**: Stricter scope format validation and wildcard handling

## [0.4.3] - 2026-01-16

### Added

- **RAJEE policies**: New integration policy for Alice to authorize `rajee-integration/` in test buckets
- **Integration tests**: Real-grants auth validation to ensure token grants drive proxy authorization

### Changed

- **Deploy workflow**: `./poe deploy` now loads and compiles policies automatically
- **RAJEE auth**: Public grants bypass is disabled by default via stack parameter
- **Policy loader**: Split multi-statement Cedar files into individual AVP policies

### Fixed

- **Cedar parsing**: Ignore line comments during policy parsing
- **Grant matching**: Wildcard grants now match in Python authorizer

## [0.4.2] - 2026-01-16

### Fixed

- **JWT issuer**: Fixed issuer claim to use only scheme+netloc (no path) for proper validation
- **Integration tests**: Refactored to use control plane `/token` endpoint, removing local JWT signing with fallback secrets

### Added

- **Auth tests**: Complete integration coverage for auth-enabled RAJEE S3 operations
- **Test policy**: `rajee_test_policy.cedar` granting `rajee-integration/` prefix access
- **Documentation**: `specs/2-rajee/12-auth-failure-analysis.md` analyzing auth failure modes

### Changed

- **RAJEE Envoy**: Auth enabled by default in deployments

## [0.4.1] - 2026-01-15

### Added

- **RAJEE Envoy**: JWT authn + Lua authz filters with prefix/wildcard grant checks
- **Control plane**: JWKS endpoint and RAJEE grants token issuance (`token_type=rajee`)
- **RAJEE grants**: Scope-to-grant conversion utilities plus unit coverage
- **Local testing**: Lua unit tests, mock JWKS server, and docker-compose harness

## [0.4.0] - 2026-01-15

### Added

- **RAJEE Envoy stack**: Dedicated S3 test bucket for proxy validation
- **RAJEE Envoy stack**: Exports `RajeeEndpoint` and `TestBucketName` for integration tests
- **Integration tests**: Envoy S3 roundtrip test (PUT/GET/DELETE) for AUTH-disabled proxy
- **Control plane**: Audit logging for compile and token issuance, plus coverage tests
- **Admin UI**: Extracted static assets (CSS/JS) into standalone files
- **Tooling**: `scripts/merge_cdk_outputs.py` to merge CDK outputs into `infra/cdk-outputs.json`

### Changed

- **RAJEE Envoy stack**: Auth gating is configurable via `AUTH_DISABLED`/`DISABLE_AUTH_CHECKS`
- **RAJEE Envoy stack**: Authorizer sidecar dependency removed for standalone proxy use
- **Local tooling**: Updated Envoy docker workflow and health checks
- **Deploy workflow**: CDK deploy writes per-stack outputs and merges them for tests

### Fixed

- **Envoy S3 proxy**: Rewrite Host header to the S3 upstream for correct request handling
- **Integration tests**: Sign RAJEE proxy requests with S3 Host header to avoid SigV4 mismatches
- **RAJEE startup**: Improved health/observability for Envoy stack

## [0.3.0] - 2026-01-14

### Added

- **RAJEE (RAJA Execution Environment)**: New testbed infrastructure for prefix-based S3 authorization
  - FastAPI authorizer service with JWT validation and prefix matching
  - Envoy proxy stack with external authorization integration
  - Docker-based local testing environment (`./poe test-docker`)
  - Flexible per-deployment architecture detection (x86_64/arm64)
  - Design specifications: RAJEE testbed, Envoy integration, and architecture review
- **Core library**: `raja.rajee.authorizer` module for prefix-based authorization logic
- **Testing**: Unit tests for RAJEE authorizer functionality
- **Infrastructure**: Platform detection utilities for CDK deployments
- **Documentation**: LOCAL_TESTING.md merged into [infra/CLAUDE.md](infra/CLAUDE.md)

### Changed

- **Poe tasks**: Improved `bump` and `test-docker` to use proper positional arguments
- **GitHub Actions**: Fixed security vulnerability in PyPI publish workflow

### Fixed

- RAJEE Envoy stack health checks now properly allow ALB traffic on port 9901
- CDK output excluded from RAJEE container asset bundles

## [0.2.3] - 2026-01-14

### Added

- **Core library modules**:
  - `exceptions.py`: Centralized exception types for consistent error handling
  - `cedar/entities.py`: Cedar entity type definitions and utilities
- **Server architecture refactoring**:
  - `server/routers/`: Modular router architecture with dedicated control plane and harness routers
  - `server/dependencies.py`: Dependency injection module for AWS resource management with caching
  - `server/logging_config.py`: Structured JSON logging for CloudWatch compatibility
  - `server/templates/admin.html`: HTML template for admin interface
- **Testing**:
  - `test_cedar_schema_parser.py`: Comprehensive Cedar schema parsing tests with edge cases
  - `test_dependencies.py`: Dependency injection and caching validation tests
- **S3 validation harness**:
  - S3 endpoints to mint, verify, and enforce RAJs without AWS calls
  - Admin UI redesign focused on S3 harness workflows
- **Tooling**:
  - Poe task `./poe all` for lint → unit tests → deploy → integration tests
  - Integration tests can read `RAJA_API_URL` from CDK output files

### Changed

- **Core library improvements**:
  - Enhanced scope parsing with better validation and wildcard support
  - Improved token operations with explicit error types
  - Strengthened enforcer with detailed error messages and logging
  - Updated compiler with improved error handling
  - Expanded public API exports in `__init__.py`
  - Added comprehensive type hints throughout
- **Server refactoring**:
  - Extracted control plane endpoints into dedicated router
  - Extracted harness/S3 endpoints into dedicated router
  - Simplified main `app.py` to focus on FastAPI setup
  - Improved code organization and maintainability
  - Enhanced testability through dependency injection
  - Better observability with structured logs
- **Infrastructure**:
  - Added `structlog>=24.1.0` to Lambda layer dependencies (fixes Runtime.ImportModuleError)
  - Updated CDK constructs for new router architecture
  - CDK deploy task writes outputs to `infra/cdk-outputs.json` using isolated output directory
  - Improved policy store configuration with better defaults

### Fixed

- **Lambda execution failure**: Added missing `structlog` dependency to Lambda layer, resolving Runtime.ImportModuleError that caused 502 errors in integration tests

### Documentation

- Added `specs/1-mvp/09-refactoring-implementation.md`: Complete refactoring documentation with architecture decisions, module organization, migration path, and lessons learned

## [0.2.1] - 2026-01-14

### Added

- **Version management automation** (`scripts/version.py`): Comprehensive version and release tooling
  - `./poe version`: Show current version from pyproject.toml
  - `./poe bump`: Bump patch version (0.2.0 → 0.2.1) and commit
  - `./poe bump-minor`: Bump minor version (0.2.0 → 0.3.0) and commit
  - `./poe bump-major`: Bump major version (0.2.0 → 1.0.0) and commit
  - `./poe tag`: Create and push git release tags with validation
    - Verifies git working directory is clean
    - Runs quality checks (`./poe check`) before tagging
    - Runs unit tests (`./poe test-unit`) before tagging
    - Supports `--recreate` flag to recreate existing tags
    - Supports `--skip-checks` flag to bypass validation (not recommended)
  - Automatic uv.lock updates when bumping versions
  - Automatic git staging and committing of version changes

### Changed

- **Release workflow** (`.github/workflows/release.yml`):
  - Added `environment: pypi` for trusted publishing to PyPI
  - Added explicit `actions/checkout@v4` step before using local setup action

### Documentation

- **CLAUDE.md**: Added comprehensive release process documentation
  - Version management workflow with semantic versioning examples
  - Release creation steps (bump, push, tag)
  - Automated release workflow explanation
  - Manual release alternatives

## [0.2.0] - 2026-01-14

### Added

#### Core Library (`src/raja/`)

- **Models** (`models.py`): Pydantic models for Scope, AuthRequest, Decision, Token, and Cedar entities
- **Scope utilities** (`scope.py`): Scope parsing, validation, and subset checking logic
- **Token operations** (`token.py`): JWT creation, verification, and validation using PyJWT
- **Compiler** (`compiler.py`): Cedar policy compilation to scope strings
- **Enforcer** (`enforcer.py`): Authorization enforcement using pure subset checking
- **Cedar parser** (`cedar/parser.py`): Cedar policy string parsing and validation
- **Cedar schema** (`cedar/schema.py`): Cedar schema definitions and entity validation

#### AWS Infrastructure (`infra/`)

- **CDK Application** (`raja_poc/app.py`): Main CDK app with stack orchestration
- **Stacks**:
  - `RajaPocStack`: Core infrastructure with API Gateway, Lambda functions, DynamoDB, Secrets Manager
  - CloudFront distribution for web interface hosting
- **Constructs**:
  - API Gateway REST API with CORS support
  - Lambda functions for compiler, enforcer, token service, and introspection
  - DynamoDB tables for policy mappings and principal scopes
  - Secrets Manager for JWT signing keys
  - S3 bucket for web interface static assets
- **Lambda Layer**: Shared Raja library layer for all Lambda functions (ARM64)
- **Lambda Handlers**:
  - `compiler/handler.py`: Compile Cedar policies to scopes via AVP
  - `enforcer/handler.py`: Authorize requests using token validation and scope checking
  - `token_service/handler.py`: Issue JWT tokens with scopes for principals
  - `introspect/handler.py`: Decode and inspect JWT token claims

#### Web Interface (`web/`)

- Interactive browser-based UI for testing RAJA
- Token request interface for different users (alice, bob, admin)
- Authorization testing with resource/action selection
- Token introspection and claim viewing
- CloudFront distribution for global access
- Configuration file for API endpoint integration

#### Testing (`tests/`)

- **Unit tests** (`unit/`): Isolated tests for all core modules (no external dependencies)
- **Integration tests** (`integration/`): AWS API endpoint validation tests
- **Hypothesis tests** (`hypothesis/`): Property-based tests validating:
  - Compilation determinism
  - Token determinism
  - Fail-closed semantics
  - Output transparency
- **Coverage**: Comprehensive test coverage with pytest-cov
- **Test markers**: `unit`, `integration`, `hypothesis`, `slow` for selective test execution

#### Scripts and Tooling

- **Deployment** (`scripts/deploy.sh`): CDK deployment helper with progress indicators
- **Policy management** (`scripts/load_policies.py`): Load Cedar policies to AVP policy store
- **Compiler invocation** (`scripts/invoke_compiler.py`): Trigger policy compilation via the control plane
- **Test data seeding** (`scripts/seed_test_data.py`): Seed DynamoDB principals for integration tests
- **Local testing** (`scripts/test_local.py`): Local development test script
- **Poe shim** (`poe`): Shell wrapper for Poe the Poet task runner

#### Control Plane (`src/raja/server/`)

- **FastAPI app** (`app.py`): Control-plane endpoints for compile/token/policies/principals/audit
- **Mangum handler** (`lambda_handlers/control_plane`): Lambda entrypoint for the control plane

#### CI/CD Workflows (`.github/workflows/`)

- **CI** (`ci.yml`): Quality checks (format, lint, typecheck), unit tests (Python 3.12/3.13, Ubuntu/macOS), build
- **Integration** (`integration.yml`): Deploy infrastructure, run integration tests, teardown (disabled)
- **Deploy** (`deploy.yml`): Automated deployment to AWS (disabled)
- **Release** (`release.yml`): Version management and GitHub releases
- **Composite action** (`setup-action`): Reusable setup for Python, UV, and dependencies

#### Documentation

- **CLAUDE.md files**: Comprehensive documentation for each major component:
  - Project root: Overall architecture and quick start
  - `src/raja/`: Core library API documentation
  - `infra/`: Infrastructure architecture and deployment
  - `lambda_handlers/`: Lambda handler specifications
  - `tests/`: Testing philosophy and structure
  - `web/`: Web interface usage guide
- **README**: User-focused documentation with quick start, examples, and architecture overview
- **CI badges**: Status badges for workflows and coverage

#### Sample Policies (`policies/`)

- Cedar schema definition with User, Document, Action entities
- Sample policies for document access control
- Policy templates for common authorization patterns

#### Project Foundation

- Initial project structure with UV Python 3.12
- Comprehensive MVP specification documents in `specs/1-mvp/`:
  - `01-mvp-spec.md`: Core RAJA/SDA hypothesis and minimal viable product definition
  - `02-mvp-cdk.md`: AWS CDK Python and Amazon Verified Permissions integration analysis
  - `03-mvp-design.md`: Detailed implementation guide with repository layout and service architecture
- Project foundation:
  - `pyproject.toml`: UV project configuration
  - `.python-version`: Python 3.12 requirement
  - `src/raja/`: Public library package structure with type hints support

### Changed

- **Python 3.12 requirement**: Set via `.python-version` and `pyproject.toml`
- **UV package manager**: Fast dependency resolution and environment management
- **Poe the Poet tasks**: Standardized task runner for all development workflows
- **Integration tooling**: Require AWS region for policy load and compiler invocation helpers
- **Control plane API**: Replaced multi-Lambda API with a FastAPI control plane
- **Admin UI**: Use stage-aware fetch URLs and disable caching to avoid stale endpoints
- **Development dependencies**: Ruff (lint/format), mypy (typecheck), pytest (test), hypothesis (property tests)
- **AWS dependencies**: boto3, aws-cdk-lib for infrastructure management
- **Lambda configuration**: ARM64 architecture for cost optimization
- **Exclusions**: Added `cdk.out/` and `web/local/` to `.gitignore` and linting exclusions
- **README structure**: Simplified to focus on user documentation and quick start

### Removed

- **Static web demo**: Removed CloudFront/S3 web stack and static web assets
- **Enforcement API**: Removed enforcer/introspect Lambda endpoints (library-first enforcement)

### Design Decisions

- **Software-Defined Authorization (SDA)**: Cedar policies compile to JWT tokens at control plane
- **Pure subset checking**: Authorization enforcement uses only scope comparison (no runtime policy evaluation)
- **Fail-closed by default**: Unknown or ambiguous requests automatically DENY
- **Output transparency**: Every decision includes the reason and relevant scopes
- **Three-part architecture**:
  1. `raja` library: Pure Python, no AWS dependencies
  2. `tests`: Unit/integration/hypothesis tests
  3. `infra`: Optional AWS CDK deployment
- **AVP as control plane**: Amazon Verified Permissions manages policies but doesn't evaluate at runtime
- **Type safety**: Full type hints with Pydantic models, mypy strict mode
- **Separation of concerns**: Core library is standalone, infrastructure is optional

## [0.1.0] - 2026-01-13

### Project Initialized

- Repository created with Apache 2.0 license
- README with project description
