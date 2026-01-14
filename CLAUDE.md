# RAJA - Resource Authorization JWT Authority

## Project Overview

**RAJA** is a Software-Defined Authorization (SDA) system that validates the hypothesis that authorization can be treated as a **compiled discipline** rather than runtime interpretation.

### Core Hypothesis

Authorization can be **compiled** once and **enforced** efficiently:

- **Control Plane:** Cedar policies → JWT tokens with explicit scopes
- **Data Plane:** Pure subset checking without policy evaluation
- **Result:** Predictable, transparent, fail-closed authorization

## Architecture

### Three-Part Design

1. **Pure Python Library** (`src/raja/`)
   - No AWS dependencies in core library
   - Can be used standalone or with AWS infrastructure
   - Full type hints and Pydantic models

2. **AWS Infrastructure** (`infra/`)
   - CDK-based deployment
   - API Gateway + Lambda + DynamoDB + AVP
   - Optional: Use library without AWS

3. **Comprehensive Testing** (`tests/`)
   - Unit tests (no external dependencies)
   - Integration tests (AWS resources)
   - Hypothesis tests (property-based validation)

### Key Components

```
┌─────────────────┐
│  Cedar Policies │  (Amazon Verified Permissions)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Compiler     │  (Cedar → Scopes)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    DynamoDB     │  (Store principal → scopes)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│  Token Service  │  (Issue JWTs with scopes)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│    Enforcer     │  (Subset checking only)
└─────────────────┘
```

## Project Structure

```
raja/
├── src/raja/              # Core library (pure Python)
│   ├── models.py          # Data models (Scope, AuthRequest, Decision, etc.)
│   ├── token.py           # JWT token operations
│   ├── enforcer.py        # Authorization enforcement (subset checking)
│   ├── compiler.py        # Policy compilation to scopes
│   ├── scope.py           # Scope parsing and operations
│   └── cedar/             # Cedar policy handling
│       ├── parser.py      # Parse Cedar policy strings
│       └── schema.py      # Cedar schema validation
│
├── infra/                 # AWS CDK infrastructure
│   └── raja_poc/
│       ├── app.py         # CDK app entry point
│       ├── stacks/        # CloudFormation stacks
│       └── constructs/    # Reusable CDK constructs
│
├── lambda_handlers/       # Lambda function handlers
│   ├── compiler/          # Policy compiler
│   ├── enforcer/          # Authorization enforcer
│   ├── token_service/     # Token issuance
│   └── introspect/        # Token introspection
│
├── tests/                 # Test suite
│   ├── unit/              # Unit tests (isolated)
│   ├── integration/       # AWS integration tests
│   └── hypothesis/        # Property-based tests
│
├── policies/              # Cedar policy definitions
│   ├── schema.cedar       # Entity and action definitions
│   └── policies/          # Policy files
│
├── scripts/               # Utility scripts
│   ├── deploy.sh          # CDK deployment helper
│   ├── load_policies.py   # Load policies to AVP
│   └── invoke_compiler.py # Trigger compiler Lambda
│
└── specs/                 # Design specifications
    └── 1-mvp/             # MVP documentation
```

See child CLAUDE.md files for detailed documentation:

- [src/raja/CLAUDE.md](src/raja/CLAUDE.md) - Core library documentation
- [infra/CLAUDE.md](infra/CLAUDE.md) - Infrastructure documentation
- [tests/CLAUDE.md](tests/CLAUDE.md) - Testing documentation
- [lambda_handlers/CLAUDE.md](lambda_handlers/CLAUDE.md) - Lambda handlers documentation

## Technology Stack

### Core Dependencies

- **Python 3.12+** - Modern Python features
- **Pydantic** (>=2.7.0) - Data validation and serialization
- **PyJWT** (>=2.8.0) - JWT token operations

### AWS (Optional)

- **AWS CDK** (>=2.100.0) - Infrastructure as Code
- **boto3** (>=1.34.0) - AWS SDK
- **Amazon Verified Permissions** - Cedar policy store

### Development Tools

- **UV** - Fast Python package manager
- **Poe the Poet** - Task runner
- **pytest** - Testing framework
- **mypy** - Static type checking
- **ruff** - Linting and formatting

## Quick Start

### Local Development

```bash
# Install dependencies
./poe install

# Run unit tests
./poe test-unit

# Run all tests
./poe test

# Format, lint, and typecheck
./poe check-all

# Watch mode for tests
./poe test-watch
```

### AWS Deployment

```bash
# Deploy infrastructure
./scripts/deploy.sh

# Load Cedar policies to AVP
python scripts/load_policies.py

# Trigger policy compilation
python scripts/invoke_compiler.py
```

## Key Concepts

### Scopes

Scopes are the fundamental unit of authorization in RAJA:

**Format:** `{ResourceType}:{ResourceId}:{Action}`

**Examples:**

- `Document:doc123:read` - Read access to document doc123
- `Document:*:read` - Read access to all documents
- `*:*:*` - Admin access (all resources, all actions)

### Compilation

Cedar policies are compiled into scope strings:

```python
from raja import compile_policy

# Cedar policy
policy = """
permit(
    principal == User::"alice",
    action == Action::"read",
    resource == Document::"doc123"
);
"""

# Compile to scopes
result = compile_policy(policy)
# result.scopes = ["Document:doc123:read"]
```

### Token Issuance

JWTs contain scopes as claims:

```python
from raja import create_token

token = create_token(
    principal="User::alice",
    scopes=["Document:doc123:read", "Document:doc456:write"],
    secret="your-secret-key"
)
```

### Enforcement

Pure subset checking - no policy evaluation:

```python
from raja import enforce

# Validate token and check authorization
decision = enforce(
    token=token,
    resource="Document::doc123",
    action="read",
    secret="your-secret-key"
)

# decision.decision = "ALLOW" or "DENY"
```

## Testing Philosophy

### Test Markers

- `@pytest.mark.unit` - Unit tests (no external dependencies)
- `@pytest.mark.integration` - AWS integration tests
- `@pytest.mark.hypothesis` - Property-based tests
- `@pytest.mark.slow` - Slow-running tests

### Property-Based Tests

RAJA uses hypothesis tests to validate core properties:

1. **Compilation Determinism** - Same policy always produces same scopes
2. **Token Determinism** - Same inputs always produce same tokens
3. **Fail-Closed Semantics** - Unknown requests default to DENY
4. **Output Transparency** - Decisions are fully explainable

### Running Tests

```bash
# Unit tests only (fast, no AWS)
./poe test-unit

# Integration tests (requires deployed AWS resources)
./poe test-integration

# Hypothesis tests (property-based validation)
./poe test-hypothesis

# All tests with coverage
./poe test-cov
```

## CI/CD Pipeline

### Workflows

1. **CI** (`.github/workflows/ci.yml`)
   - Format check → Lint → Typecheck → Unit tests → Build
   - Runs on every push and PR

2. **Integration** (`.github/workflows/integration.yml`)
   - Deploys infrastructure
   - Runs integration tests
   - Tears down resources

3. **Deploy** (`.github/workflows/deploy.yml`)
   - Automated deployment to AWS
   - Triggered on main branch

4. **Release** (`.github/workflows/release.yml`)
   - Version management
   - GitHub releases
   - Package publishing

## Configuration

### Environment Variables (Lambda)

- `POLICY_STORE_ID` - AVP policy store identifier
- `MAPPINGS_TABLE` - DynamoDB table for policy-to-scope mappings
- `PRINCIPAL_TABLE` - DynamoDB table for principal-to-scope mappings
- `JWT_SECRET_ARN` - Secrets Manager ARN for JWT signing key

### Poe Tasks

See `pyproject.toml` for full task definitions:

```bash
./poe install             # Install package locally
./poe test                # Run all tests
./poe test-unit           # Unit tests only
./poe test-integration    # Integration tests only
./poe test-hypothesis     # Hypothesis tests only
./poe test-cov            # Tests with coverage
./poe test-watch          # Watch mode
./poe format              # Format code
./poe lint                # Lint code
./poe typecheck           # Type check
./poe check-all           # Format + lint + typecheck
./poe cdk-synth           # Synthesize CDK
./poe cdk-deploy          # Deploy CDK
./poe cdk-destroy         # Destroy CDK
./poe version             # Show current version
./poe bump                # Bump patch version and commit
./poe bump-minor          # Bump minor version and commit
./poe bump-major          # Bump major version and commit
./poe tag                 # Create and push release tag
```

## Release Process

### Version Management

RAJA uses semantic versioning (MAJOR.MINOR.PATCH):

```bash
# Show current version
./poe version

# Bump patch version (0.2.0 → 0.2.1) - for bug fixes
./poe bump

# Bump minor version (0.2.0 → 0.3.0) - for new features
./poe bump-minor

# Bump major version (0.2.0 → 1.0.0) - for breaking changes
./poe bump-major
```

These commands automatically:

1. Update version in [pyproject.toml](pyproject.toml)
2. Update `uv.lock` if it exists
3. Stage and commit the changes

### Creating a Release

To create a new release:

```bash
# 1. Bump version and commit
./poe bump        # or bump-minor / bump-major

# 2. Push the version bump
git push

# 3. Create and push the release tag
./poe tag
```

The `./poe tag` command will:

1. Verify git working directory is clean
2. Read version from pyproject.toml
3. Run quality checks (`./poe check`)
4. Run unit tests (`./poe test-unit`)
5. Create git tag `vX.Y.Z`
6. Push tag to origin

**Additional options:**

```bash
# Skip quality checks and tests (not recommended)
./poe tag -- --skip-checks

# Recreate an existing tag (deletes old tag first)
./poe tag -- --recreate

# Combine flags
./poe tag -- --recreate --skip-checks
```

### What Happens After Tagging

Once the tag is pushed, the GitHub Actions release workflow automatically:

1. Verifies tag matches pyproject.toml version
2. Runs quality checks and tests
3. Builds the package
4. Publishes to PyPI
5. Uploads release assets to GitHub
6. Updates release notes from CHANGELOG.md

### Manual Release (Alternative)

If you prefer to create the tag manually:

```bash
# Create tag
git tag vX.Y.Z

# Push tag
git push origin vX.Y.Z
```

Or create a GitHub Release directly through the web interface.

## Design Principles

### 1. Fail-Closed by Default

Unknown or ambiguous requests automatically DENY. Never guess or assume permissions.

### 2. Compilation Over Interpretation

Policies are compiled once, enforced many times. No runtime policy evaluation.

### 3. Pure Subset Checking

Enforcement is purely checking if requested scope is a subset of granted scopes.

### 4. Output Transparency

Every decision includes the reason and relevant scopes.

### 5. Type Safety

Full type hints with Pydantic models. Mypy strict mode enabled.

### 6. Separation of Concerns

- Core library is pure Python (no AWS dependencies)
- Infrastructure is optional
- Testing is comprehensive and isolated

## API Endpoints (AWS Deployment)

When deployed to AWS, RAJA exposes these endpoints:

- `POST /token` - Issue JWT tokens with scopes
- `POST /authorize` - Check authorization (enforce)
- `GET /introspect` - Decode and inspect token claims
- `GET /health` - Health check

## Contributing

1. Follow the existing code style (enforced by ruff)
2. Add tests for new features
3. Ensure `./poe check-all` passes
4. Update documentation as needed

## Resources

- **Specs:** See `specs/1-mvp/` for detailed design documents
- **Cedar:** <https://www.cedarpolicy.com/>
- **Amazon Verified Permissions:** <https://aws.amazon.com/verified-permissions/>

## License

See LICENSE file for details.
