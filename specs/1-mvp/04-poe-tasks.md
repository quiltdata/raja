# RAJA Poe Task Configuration
## Developer Workflow Automation

## Purpose

This document specifies the Poe the Poet task runner configuration for RAJA. Poe provides a standardized way to run common development tasks across the project.

---

## Why Poe the Poet?

**Benefits:**
- Task definitions in `pyproject.toml` (single source of truth)
- Cross-platform compatibility (works on macOS, Linux, Windows)
- Simple syntax for common tasks
- Can be invoked via `poe` command or `./poe` shim
- No need for Makefiles or shell scripts

**Philosophy:**
- Every common development task should have a poe task
- Tasks should be self-documenting with descriptions
- Tasks should work from any directory in the repo
- CI/CD pipelines should use the same poe tasks as developers

---

## Poe Shim Script

### `./poe` - Repository Root Shim

**Purpose**: Allow running `./poe <task>` without installing poe globally

**Location**: `/Users/ernest/GitHub/raja/poe`

**Behavior**:
```bash
#!/usr/bin/env bash
# Poe the Poet shim script
# Usage: ./poe <task> [args...]

# Use UV to run poe within the project environment
exec uv run poe "$@"
```

**Permissions**: Executable (`chmod +x poe`)

**Usage**:
```bash
./poe test          # Run tests
./poe lint          # Run linters
./poe format        # Format code
```

---

## Task Categories

### 1. Code Quality Tasks
### 2. Testing Tasks
### 3. Build and Package Tasks
### 4. Infrastructure Tasks
### 5. Development Helpers

---

## Task Definitions

All tasks defined in `pyproject.toml` under `[tool.poe.tasks]`

---

## 1. Code Quality Tasks

### `format` - Format Code
**Description**: Auto-format Python code with ruff

**Command**: `ruff format src tests infra lambda_handlers`

**When to use**: Before committing code

**Example**:
```bash
./poe format
```

---

### `lint` - Run Linters
**Description**: Check code quality with ruff linter

**Command**: `ruff check src tests infra lambda_handlers`

**When to use**: Before committing, in CI

**Example**:
```bash
./poe lint
```

---

### `lint-fix` - Auto-fix Linting Issues
**Description**: Automatically fix linting issues where possible

**Command**: `ruff check --fix src tests infra lambda_handlers`

**When to use**: To fix simple linting errors automatically

**Example**:
```bash
./poe lint-fix
```

---

### `typecheck` - Type Checking
**Description**: Run mypy type checker on source code

**Command**: `mypy src`

**When to use**: Before committing, in CI

**Example**:
```bash
./poe typecheck
```

---

### `check` - Run All Quality Checks
**Description**: Run lint + typecheck in sequence

**Command**: Composite task running `lint` then `typecheck`

**When to use**: Before pushing, in CI

**Example**:
```bash
./poe check
```

---

## 2. Testing Tasks

### `test` - Run All Tests
**Description**: Run full test suite with pytest

**Command**: `pytest tests/ -v`

**When to use**: Default test command, in CI

**Example**:
```bash
./poe test
```

---

### `test-unit` - Run Unit Tests Only
**Description**: Run only unit tests (fast, no AWS)

**Command**: `pytest tests/unit/ -v`

**When to use**: During development for quick feedback

**Example**:
```bash
./poe test-unit
```

---

### `test-integration` - Run Integration Tests
**Description**: Run integration tests against AWS services

**Command**: `pytest tests/integration/ -v`

**Requirements**: AWS credentials, deployed infrastructure

**When to use**: After deployment, in CI

**Example**:
```bash
./poe test-integration
```

---

### `test-hypothesis` - Run Hypothesis Validation Tests
**Description**: Run tests that validate RAJA/SDA hypothesis

**Command**: `pytest tests/hypothesis/ -v`

**When to use**: After deployment, for MVP validation

**Example**:
```bash
./poe test-hypothesis
```

---

### `test-cov` - Run Tests with Coverage
**Description**: Run tests and generate coverage report

**Command**: `pytest tests/ --cov=src/raja --cov-report=html --cov-report=term`

**When to use**: To measure test coverage

**Example**:
```bash
./poe test-cov
```

---

### `test-watch` - Run Tests in Watch Mode
**Description**: Run tests automatically on file changes

**Command**: `pytest-watch tests/ -- -v`

**Requirements**: `pytest-watch` package

**When to use**: During active development

**Example**:
```bash
./poe test-watch
```

---

## 3. Build and Package Tasks

### `build` - Build Package
**Description**: Build the raja package distribution

**Command**: `uv build`

**Output**: Creates `dist/` directory with wheel and sdist

**When to use**: Before publishing

**Example**:
```bash
./poe build
```

---

### `clean` - Clean Build Artifacts
**Description**: Remove build artifacts and cache directories

**Command**: `rm -rf dist/ build/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov/`

**When to use**: To start fresh, before building

**Example**:
```bash
./poe clean
```

---

### `install` - Install Package Locally
**Description**: Install raja package in editable mode

**Command**: `uv pip install -e .`

**When to use**: For local development and testing

**Example**:
```bash
./poe install
```

---

## 4. Infrastructure Tasks

### `cdk-synth` - Synthesize CDK Stack
**Description**: Generate CloudFormation templates from CDK code

**Command**: `cd infra && cdk synth`

**Requirements**: AWS CDK installed, AWS credentials

**When to use**: To preview CloudFormation before deployment

**Example**:
```bash
./poe cdk-synth
```

---

### `cdk-diff` - Show CDK Changes
**Description**: Show what will change in AWS infrastructure

**Command**: `cd infra && cdk diff`

**When to use**: Before deploying to see changes

**Example**:
```bash
./poe cdk-diff
```

---

### `cdk-deploy` - Deploy CDK Stack
**Description**: Deploy raja-poc infrastructure to AWS

**Command**: `cd infra && cdk deploy --all --require-approval never`

**Requirements**: AWS credentials with deployment permissions

**When to use**: To deploy or update infrastructure

**Example**:
```bash
./poe cdk-deploy
```

---

### `cdk-destroy` - Destroy CDK Stack
**Description**: Remove all AWS infrastructure

**Command**: `cd infra && cdk destroy --all --force`

**Warning**: Destructive operation

**When to use**: To tear down infrastructure

**Example**:
```bash
./poe cdk-destroy
```

---

### `load-policies` - Load Cedar Policies to AVP
**Description**: Upload Cedar policies from policies/ to AVP Policy Store

**Command**: `python scripts/load_policies.py`

**Requirements**: Deployed AVP Policy Store

**When to use**: After deploying infrastructure, when policies change

**Example**:
```bash
./poe load-policies
```

---

### `compile-policies` - Compile Policies to Scopes
**Description**: Invoke PolicyCompiler Lambda to generate scope mappings

**Command**: `python scripts/invoke_compiler.py`

**Requirements**: Deployed Lambda function

**When to use**: After loading policies

**Example**:
```bash
./poe compile-policies
```

---

## 5. Development Helpers

### `docs` - Generate Documentation
**Description**: Build documentation with Sphinx (future)

**Command**: `cd docs && make html`

**Status**: Placeholder for future implementation

**Example**:
```bash
./poe docs
```

---

### `repl` - Start Python REPL
**Description**: Start Python REPL with raja package loaded

**Command**: `uv run python`

**When to use**: For interactive testing and exploration

**Example**:
```bash
./poe repl
```

---

### `shell` - Start Project Shell
**Description**: Start shell with project environment activated

**Command**: `uv run bash`

**When to use**: For running multiple commands in project environment

**Example**:
```bash
./poe shell
```

---

## Task Composition

### Sequential Execution
**Example**: Run format, then lint, then typecheck
```toml
[tool.poe.tasks.check-all]
sequence = ["format", "lint", "typecheck"]
```

### Parallel Execution (Future)
**Example**: Run multiple test suites in parallel
```toml
[tool.poe.tasks.test-all-parallel]
parallel = ["test-unit", "test-integration"]
```

---

## Complete pyproject.toml Configuration

### Dependencies Section
```toml
[project]
# ... existing project config ...

dependencies = [
    "pyjwt>=2.8.0",
    "pydantic>=2.5.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-cov>=4.1.0",
    "pytest-watch>=4.2.0",
    "ruff>=0.1.0",
    "mypy>=1.7.0",
    "poethepoet>=0.24.0",
]

aws = [
    "boto3>=1.34.0",
    "aws-cdk-lib>=2.100.0",
    "constructs>=10.0.0",
]

test = [
    "pytest>=7.4.0",
    "pytest-cov>=4.1.0",
    "moto>=4.2.0",  # AWS mocking
]
```

### Poe Tasks Section
```toml
[tool.poe.tasks]

# Code quality
format = { cmd = "ruff format src tests infra lambda_handlers", help = "Format code with ruff" }
lint = { cmd = "ruff check src tests infra lambda_handlers", help = "Lint code with ruff" }
lint-fix = { cmd = "ruff check --fix src tests infra lambda_handlers", help = "Auto-fix lint issues" }
typecheck = { cmd = "mypy src", help = "Run type checker" }
check = { sequence = ["lint", "typecheck"], help = "Run all quality checks" }

# Testing
test = { cmd = "pytest tests/ -v", help = "Run all tests" }
test-unit = { cmd = "pytest tests/unit/ -v", help = "Run unit tests only" }
test-integration = { cmd = "pytest tests/integration/ -v", help = "Run integration tests" }
test-hypothesis = { cmd = "pytest tests/hypothesis/ -v", help = "Run hypothesis validation tests" }
test-cov = { cmd = "pytest tests/ --cov=src/raja --cov-report=html --cov-report=term", help = "Run tests with coverage" }
test-watch = { cmd = "pytest-watch tests/ -- -v", help = "Run tests in watch mode" }

# Build
build = { cmd = "uv build", help = "Build package" }
clean = { cmd = "rm -rf dist/ build/ *.egg-info .pytest_cache .mypy_cache .ruff_cache htmlcov/", help = "Clean build artifacts" }
install = { cmd = "uv pip install -e .", help = "Install package locally" }

# Infrastructure
cdk-synth = { cmd = "cd infra && cdk synth", help = "Synthesize CDK stack" }
cdk-diff = { cmd = "cd infra && cdk diff", help = "Show CDK changes" }
cdk-deploy = { cmd = "cd infra && cdk deploy --all --require-approval never", help = "Deploy CDK stack" }
cdk-destroy = { cmd = "cd infra && cdk destroy --all --force", help = "Destroy CDK stack" }
load-policies = { cmd = "python scripts/load_policies.py", help = "Load Cedar policies to AVP" }
compile-policies = { cmd = "python scripts/invoke_compiler.py", help = "Compile policies to scopes" }

# Development
repl = { cmd = "uv run python", help = "Start Python REPL" }
shell = { cmd = "uv run bash", help = "Start project shell" }
```

### Ruff Configuration
```toml
[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
    "UP",  # pyupgrade
]
ignore = []

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
```

### Mypy Configuration
```toml
[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
disallow_untyped_defs = true
disallow_any_generics = true
check_untyped_defs = true
no_implicit_optional = true
warn_redundant_casts = true
warn_unused_ignores = true
warn_no_return = true
strict_equality = true
```

### Pytest Configuration
```toml
[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]
addopts = [
    "--strict-markers",
    "--strict-config",
    "--showlocals",
]
markers = [
    "unit: Unit tests (no external dependencies)",
    "integration: Integration tests (require AWS)",
    "hypothesis: RAJA hypothesis validation tests",
    "slow: Slow-running tests",
]
```

---

## Common Workflows

### Development Workflow
```bash
# 1. Make code changes
# 2. Format code
./poe format

# 3. Check quality
./poe check

# 4. Run unit tests
./poe test-unit

# 5. Commit changes
git add .
git commit -m "feat: add new feature"
```

### Deployment Workflow
```bash
# 1. Check infrastructure changes
./poe cdk-diff

# 2. Deploy infrastructure
./poe cdk-deploy

# 3. Load policies
./poe load-policies

# 4. Compile policies
./poe compile-policies

# 5. Run integration tests
./poe test-integration

# 6. Validate hypothesis
./poe test-hypothesis
```

### Pre-commit Workflow
```bash
# Run all checks before committing
./poe format
./poe check
./poe test-unit
```

### CI Workflow
```bash
# Same tasks as local development
./poe check
./poe test
./poe build
```

---

## Installation Instructions

### For Developers

**Step 1**: Install dependencies including dev tools
```bash
uv sync --extra dev
```

**Step 2**: Make poe shim executable
```bash
chmod +x poe
```

**Step 3**: Verify installation
```bash
./poe --help
```

### For CI/CD

**In CI config**: Use `./poe` directly (no global installation needed)
```yaml
- run: ./poe check
- run: ./poe test
- run: ./poe build
```

---

## Benefits of This Approach

### 1. Consistency
- Developers and CI use identical commands
- No discrepancy between local and CI environments

### 2. Discoverability
- `./poe --help` shows all available tasks
- Task descriptions self-document workflows

### 3. Simplicity
- Single `./poe` entry point for all tasks
- No need to remember complex command arguments

### 4. Maintainability
- Task definitions in version control (pyproject.toml)
- Changes to tasks propagate to all environments

### 5. Cross-platform
- Works on macOS, Linux, Windows
- No bash-specific syntax required

---

## Future Enhancements

### 1. Task Dependencies
```toml
[tool.poe.tasks.deploy-full]
sequence = ["build", "cdk-deploy", "load-policies", "compile-policies", "test-integration"]
```

### 2. Environment Variables
```toml
[tool.poe.tasks.deploy-prod]
env = { ENV = "production", AWS_REGION = "us-east-1" }
cmd = "cd infra && cdk deploy"
```

### 3. Interactive Tasks
```toml
[tool.poe.tasks.choose-env]
script = "scripts.interactive:choose_environment"
```

### 4. Pre-commit Hook Integration
```bash
# .git/hooks/pre-commit
#!/bin/bash
./poe format
./poe check
./poe test-unit
```

---

## Summary

This Poe the Poet configuration provides:
- ✅ **20+ standardized tasks** for all development workflows
- ✅ **`./poe` shim** for easy execution without global installation
- ✅ **Self-documenting** commands with help text
- ✅ **CI/CD ready** - same commands locally and in automation
- ✅ **Comprehensive coverage** - quality, testing, build, infrastructure, development

Next: CI/CD specification that uses these poe tasks.
