# Changelog

All notable changes to the RAJA project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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
- **Compiler invocation** (`scripts/invoke_compiler.py`): Trigger policy compilation Lambda
- **Local testing** (`scripts/test_local.py`): Local development test script
- **Poe shim** (`poe`): Shell wrapper for Poe the Poet task runner

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
- **Development dependencies**: Ruff (lint/format), mypy (typecheck), pytest (test), hypothesis (property tests)
- **AWS dependencies**: boto3, aws-cdk-lib for infrastructure management
- **Lambda configuration**: ARM64 architecture for cost optimization
- **Exclusions**: Added `cdk.out/` and `web/local/` to `.gitignore` and linting exclusions
- **README structure**: Simplified to focus on user documentation and quick start

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
