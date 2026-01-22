# RAJA Testing Architecture

This document explains the testing philosophy and structure of the RAJA test suite.

## Testing Philosophy

RAJA follows a **defense-in-depth** testing strategy with multiple layers that serve different purposes. This is **intentional redundancy** - the same concepts are tested at different layers to catch different types of failures.

### Key Principles

1. **Fail-closed by default** - Authorization failures are expected and tested
2. **Multiple layers** - Unit → Integration → Demo → GUI
3. **Shared utilities** - Common code in `tests/shared/`
4. **Property-based testing** - Hypothesis tests validate invariants
5. **Type safety** - Full type hints with mypy strict mode

## Test Layer Overview

| Layer | Tests | Purpose | Speed | Dependencies |
|-------|-------|---------|-------|--------------|
| **Unit** | 157 | Core logic validation | Fast (seconds) | Pure Python |
| **Integration** | 32 | AWS deployment validation | Slow (minutes) | Deployed AWS infra |
| **Demo** | 5 | Proof-of-concept showcase | Slow (minutes) | Deployed AWS infra |
| **Admin GUI** | 6/31 | Interactive exploration | Fast (seconds) | Local harness |

**Total:** 189+ automated test functions

## Test Layers Explained

### 1. Unit Tests (`tests/unit/`)

**Purpose:** Validate core library logic in isolation

**Coverage:**
- Token creation, decoding, validation (no AWS)
- Scope parsing and matching logic
- Cedar policy parsing
- Compilation logic (Cedar → scopes)
- Enforcement logic (pure subset checking)
- Model validation (Pydantic)

**Run:**
```bash
./poe test-unit  # Fast, no AWS dependencies
```

**When to use:**
- Testing pure Python logic
- Fast feedback during development
- No external dependencies needed

**Example files:**
- `test_token.py` - JWT operations
- `test_scope.py` - Scope matching
- `test_enforcer.py` - Authorization logic
- `test_cedar_parser.py` - Cedar parsing

---

### 2. Integration Tests (`tests/integration/`)

**Purpose:** Validate deployed AWS infrastructure end-to-end

**Coverage:**
- Token security through Envoy (expired, invalid sig, malformed)
- Policy compilation (Cedar → scopes → DynamoDB)
- Scope enforcement through Envoy proxy
- Request parsing through S3 proxy
- Cross-component consistency
- Operational scenarios (secrets rotation, clock skew, rate limiting)

**Run:**
```bash
./poe test-integration  # Requires deployed AWS resources
```

**When to use:**
- Validating AWS deployment
- Testing full stack behavior
- Pre-deployment verification

**Files:**
- `test_rajee_envoy_bucket.py` - S3 proxy operations
- `test_failure_modes.py` - Security failure scenarios
- `test_control_plane.py` - Control plane APIs
- `test_token_service.py` - Token issuance

---

### 3. Demo (`./poe demo`)

**Purpose:** Polished proof-of-concept demonstration

**What it runs:**
```bash
pytest tests/integration/test_rajee_envoy_bucket.py -v -s
```

**Coverage:**
- Basic S3 operations (bucket check, PUT/GET/DELETE)
- Authorization with real scopes from policies
- Authorization denial for unauthorized prefixes
- ListBucket, GetObjectAttributes, versioning

**Run:**
```bash
./poe demo  # Verbose output with formatted logging
```

**When to use:**
- Showcasing RAJA to stakeholders
- Verifying end-to-end functionality
- Generating demo output for documentation

**Key difference from integration tests:** This is a **curated subset** with **polished console output** for presentation purposes.

---

### 4. Admin GUI (`src/raja/server/`)

**Purpose:** Interactive developer tool for manual exploration

**Coverage (implemented):**
- Token security tests (expired, invalid sig, malformed)
- Token claim validation
- Missing/empty scopes

**Coverage (planned but not implemented):**
- Cedar compilation failures (7 tests)
- Scope enforcement failures (8 tests)
- Request parsing failures (5 tests)
- Cross-component failures (6 tests)
- Operational failures (7 tests)

**Run:**
```bash
./poe admin  # Start admin server
# Then navigate to http://localhost:8000/admin
```

**When to use:**
- Quick manual testing during development
- Exploring edge cases interactively
- Visual validation of failure modes
- Debugging authorization issues

**Files:**
- `src/raja/server/routers/failure_tests.py` - Backend API
- `src/raja/server/templates/admin.html` - UI
- `src/raja/server/static/admin.js` - Frontend logic

---

## Why Test the Same Thing Multiple Times?

### Scope Enforcement Example

Scope enforcement is tested in **four different layers**:

1. **Unit tests** (`test_scope.py`, `test_enforcer.py`)
   - **Purpose:** Validate pure matching logic
   - **What it catches:** Logic errors, edge cases in string matching
   - **Speed:** Milliseconds

2. **Integration tests** (`test_failure_modes.py`)
   - **Purpose:** Validate through AWS infrastructure
   - **What it catches:** Envoy configuration issues, Lua script bugs, deployment errors
   - **Speed:** Seconds to minutes

3. **Admin GUI** (`failure_tests.py`)
   - **Purpose:** Interactive exploration
   - **What it catches:** User-reported edge cases, undocumented behaviors
   - **Speed:** Real-time feedback

4. **Hypothesis tests** (property-based)
   - **Purpose:** Validate invariants hold across inputs
   - **What it catches:** Unexpected input combinations, boundary conditions
   - **Speed:** Seconds

### This is NOT duplication - it's **defense in depth**

Each layer:
- Tests the same **concept** but different **implementations**
- Catches different **classes of bugs**
- Serves different **audiences** (developers vs. DevOps vs. security)
- Has different **tradeoffs** (speed vs. coverage vs. ergonomics)

## Shared Utilities (`tests/shared/`)

To reduce **actual duplication** (copy-pasted code), we provide shared utilities:

### `token_builder.py` - Unified Token Construction

**Purpose:** Eliminate duplicated JWT building logic

**Usage:**
```python
from tests.shared.token_builder import TokenBuilder

# Build token with fluent API
token = (
    TokenBuilder(secret=secret, issuer=issuer, audience="raja-s3-proxy")
    .with_subject("User::alice")
    .with_scopes(["S3Object:bucket/key:s3:GetObject"])
    .with_ttl(3600)
    .build()
)

# For expired tokens
token = builder.with_expiration_in_past(seconds_ago=60).build()

# For missing claims
token = builder.without_scopes().build()
```

**Replaces:**
- `tests/integration/test_failure_modes.py::_build_token()`
- `src/raja/server/routers/failure_tests.py::_build_token()`
- `tests/local/generate_test_token.py::generate_token()`

---

### `s3_client.py` - Unified S3 Client Setup

**Purpose:** Eliminate duplicated S3 client configuration

**Usage:**
```python
from tests.shared.s3_client import create_rajee_s3_client

# Create S3 client configured for RAJEE Envoy proxy
s3, bucket = create_rajee_s3_client(token=token, verbose=True)

# Use client
s3.put_object(Bucket=bucket, Key="test.txt", Body=b"hello")
```

**Replaces:**
- `tests/integration/test_rajee_envoy_bucket.py::_create_s3_client_with_rajee_proxy()`
- `tests/integration/test_failure_modes.py::_create_s3_client_with_rajee_proxy()`

---

## Running Tests

### Quick Commands

```bash
# All tests (unit + integration)
./poe test

# Unit tests only (fast, no AWS)
./poe test-unit

# Integration tests (requires deployed AWS)
./poe test-integration

# All tests with coverage report
./poe test-cov

# Demo (verbose output)
./poe demo

# Admin GUI
./poe admin
```

### Running Specific Tests

```bash
# Run specific test file
pytest tests/unit/test_token.py -v

# Run specific test function
pytest tests/unit/test_token.py::test_create_token -v

# Run tests matching a pattern
pytest -k "expired" -v

# Run tests by marker
pytest -m integration -v
```

### Test Markers

```python
@pytest.mark.unit           # Unit test (no external deps)
@pytest.mark.integration    # Integration test (requires AWS)
@pytest.mark.hypothesis     # Property-based test
@pytest.mark.slow           # Slow-running test
```

## Writing New Tests

### Unit Test Template

```python
import pytest
from raja import create_token, decode_token

@pytest.mark.unit
def test_token_roundtrip() -> None:
    """Test that tokens can be created and decoded."""
    secret = "test-secret"
    token = create_token(
        principal="User::alice",
        scopes=["Document:doc123:read"],
        secret=secret,
    )

    decoded = decode_token(token, secret=secret)

    assert decoded["principal"] == "User::alice"
    assert decoded["scopes"] == ["Document:doc123:read"]
```

### Integration Test Template

```python
import pytest
from tests.shared.token_builder import TokenBuilder
from tests.shared.s3_client import create_rajee_s3_client
from tests.integration.helpers import fetch_jwks_secret, require_api_issuer

@pytest.mark.integration
def test_s3_authorization() -> None:
    """Test S3 operation with valid authorization."""
    secret = fetch_jwks_secret()
    issuer = require_api_issuer()

    token = (
        TokenBuilder(secret=secret, issuer=issuer, audience="raja-s3-proxy")
        .with_subject("User::test")
        .with_scopes(["S3Object:bucket/uploads/:s3:PutObject"])
        .build()
    )

    s3, bucket = create_rajee_s3_client(token=token)

    # Should succeed
    s3.put_object(Bucket=bucket, Key="uploads/test.txt", Body=b"hello")

    # Should fail (different prefix)
    with pytest.raises(ClientError):
        s3.put_object(Bucket=bucket, Key="private/test.txt", Body=b"hello")
```

## Test Coverage Goals

| Component | Target | Current | Status |
|-----------|--------|---------|--------|
| Core library (`src/raja/`) | 90%+ | 85% | 🟡 Good |
| Token operations | 95%+ | 98% | ✅ Excellent |
| Scope enforcement | 95%+ | 92% | 🟡 Good |
| Cedar parsing | 80%+ | 75% | 🟡 Good |
| Compilation | 85%+ | 82% | 🟡 Good |
| Integration tests | All critical paths | 90% | ✅ Good |

## Troubleshooting

### "ImportError: cannot import name 'TokenBuilder'"

**Cause:** Python path not set up correctly

**Solution:**
```python
# In test files, use relative imports
from ..shared.token_builder import TokenBuilder
```

### "Connection refused" during integration tests

**Cause:** AWS infrastructure not deployed

**Solution:**
```bash
./poe deploy  # Deploy infrastructure first
./poe test-integration  # Then run integration tests
```

### "RAJEE_ENDPOINT not set"

**Cause:** Missing environment variables for integration tests

**Solution:**
```bash
# Integration tests read from cdk-outputs.json
./poe deploy  # Deployment creates this file
```

### Tests pass locally but fail in CI

**Cause:** AWS credentials or environment differences

**Solution:**
- Check GitHub Actions secrets are configured
- Ensure CDK outputs are uploaded as artifacts
- Verify AWS region consistency

## Related Documentation

- **Main docs:** [CLAUDE.md](../CLAUDE.md) - Project overview
- **Failure modes:** [specs/3-schema/03-failure-modes.md](../specs/3-schema/03-failure-modes.md)
- **Admin GUI spec:** [specs/3-schema/07-enhance-admin.md](../specs/3-schema/07-enhance-admin.md)
- **Integration tests:** [tests/integration/CLAUDE.md](integration/CLAUDE.md)

## Contributing

When adding new tests:

1. **Choose the right layer:**
   - Pure logic → Unit test
   - AWS integration → Integration test
   - Manual exploration → Admin GUI
   - Invariant validation → Hypothesis test

2. **Use shared utilities:**
   - Token building → `TokenBuilder`
   - S3 client → `create_rajee_s3_client()`
   - Test fixtures → `tests/integration/helpers.py`

3. **Add appropriate markers:**
   ```python
   @pytest.mark.unit           # No external deps
   @pytest.mark.integration    # Requires AWS
   @pytest.mark.slow           # Takes >1 second
   ```

4. **Document complex tests:**
   - Add docstrings explaining what is being tested
   - Comment on expected vs. actual behavior
   - Link to relevant specs or issues

5. **Verify all layers pass:**
   ```bash
   ./poe check-all  # Format + lint + typecheck + test
   ```

## Summary

The RAJA test suite is **intentionally multi-layered** with:
- **4 distinct test layers** serving different purposes
- **189+ automated tests** providing comprehensive coverage
- **Shared utilities** to eliminate code duplication
- **Defense-in-depth** approach to catch bugs at multiple levels

This is **not wasteful duplication** - it's a **well-architected testing strategy** that balances speed, coverage, and maintainability.
