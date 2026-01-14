# RAJA Testing Documentation

## Overview

RAJA has a comprehensive test suite covering unit tests, integration tests, and property-based validation tests. The testing philosophy emphasizes isolation, determinism, and validation of core authorization properties.

## Test Structure

```
tests/
├── __init__.py
├── conftest.py                    # Shared pytest fixtures
├── unit/                          # Unit tests (no external dependencies)
│   ├── __init__.py
│   ├── test_models.py             # Pydantic model tests
│   ├── test_token.py              # JWT token operations
│   ├── test_enforcer.py           # Authorization enforcement
│   ├── test_compiler.py           # Policy compilation
│   ├── test_scope.py              # Scope operations
│   ├── test_cedar_parser.py       # Cedar policy parsing
│   └── test_cedar_schema.py       # Cedar schema validation
│
├── integration/                   # AWS integration tests
│   ├── __init__.py
│   ├── conftest.py                # Integration test fixtures
│   ├── helpers.py                 # Test helper functions
│   ├── test_end_to_end.py         # Full authorization flow
│   ├── test_policy_store.py       # AVP policy store operations
│   ├── test_token_service.py      # Token service Lambda
│   └── test_enforcement_service.py # Enforcer Lambda
│
└── hypothesis/                    # Property-based tests
    ├── __init__.py
    ├── test_compilation.py        # Compilation determinism
    ├── test_determinism.py        # Token/scope determinism
    ├── test_fail_closed.py        # Fail-closed semantics
    └── test_transparency.py       # Output transparency
```

## Test Markers

RAJA uses pytest markers to categorize tests:

```python
@pytest.mark.unit           # Unit test (no external dependencies)
@pytest.mark.integration    # Integration test (requires AWS)
@pytest.mark.hypothesis     # Property-based validation test
@pytest.mark.slow          # Slow-running test (> 1 second)
```

### Running Tests by Marker

```bash
# Unit tests only (fast, no AWS)
./poe test-unit
# or
pytest -m unit

# Integration tests only (requires AWS)
./poe test-integration
# or
pytest -m integration

# Hypothesis tests only
./poe test-hypothesis
# or
pytest -m hypothesis

# All tests except slow ones
pytest -m "not slow"
```

## Unit Tests

Unit tests are **pure Python** with no external dependencies (no AWS, no network calls, no filesystem access beyond reading test fixtures).

### Test Coverage

#### 1. Models (`test_models.py`)

Tests for Pydantic models:

```python
def test_scope_from_string():
    """Test Scope.from_string() parsing"""
    scope = Scope.from_string("Document:doc123:read")
    assert scope.resource_type == "Document"
    assert scope.resource_id == "doc123"
    assert scope.action == "read"

def test_scope_to_string():
    """Test Scope.to_string() formatting"""
    scope = Scope(resource_type="Document", resource_id="doc123", action="read")
    assert scope.to_string() == "Document:doc123:read"

def test_scope_is_subset_of():
    """Test scope subset checking"""
    specific = Scope.from_string("Document:doc123:read")
    wildcard = Scope.from_string("Document:*:read")
    assert specific.is_subset_of(wildcard)
```

#### 2. Token Operations (`test_token.py`)

Tests for JWT token creation and validation:

```python
def test_create_token():
    """Test token creation with scopes"""
    token = create_token(
        principal="User::alice",
        scopes=["Document:doc123:read"],
        secret="test-secret"
    )
    assert isinstance(token, str)
    assert len(token) > 0

def test_decode_token():
    """Test token decoding and validation"""
    token = create_token(
        principal="User::alice",
        scopes=["Document:doc123:read"],
        secret="test-secret"
    )
    decoded = decode_token(token, "test-secret")
    assert decoded.principal == "User::alice"
    assert decoded.scopes == ["Document:doc123:read"]

def test_expired_token():
    """Test that expired tokens raise error"""
    # Create token with negative expiration
    token = create_token(
        principal="User::alice",
        scopes=["Document:doc123:read"],
        secret="test-secret",
        expiration_minutes=-1
    )
    with pytest.raises(jwt.ExpiredSignatureError):
        decode_token(token, "test-secret")
```

#### 3. Enforcement (`test_enforcer.py`)

Tests for authorization enforcement logic:

```python
def test_enforce_allow():
    """Test successful authorization"""
    token = create_token(
        principal="User::alice",
        scopes=["Document:doc123:read"],
        secret="test-secret"
    )
    decision = enforce(
        token=token,
        resource="Document::doc123",
        action="read",
        secret="test-secret"
    )
    assert decision.decision == "ALLOW"

def test_enforce_deny():
    """Test denied authorization"""
    token = create_token(
        principal="User::alice",
        scopes=["Document:doc123:read"],
        secret="test-secret"
    )
    decision = enforce(
        token=token,
        resource="Document::doc456",  # Different document
        action="read",
        secret="test-secret"
    )
    assert decision.decision == "DENY"

def test_enforce_wildcard():
    """Test wildcard scope matching"""
    token = create_token(
        principal="User::alice",
        scopes=["Document:*:read"],  # Wildcard
        secret="test-secret"
    )
    decision = enforce(
        token=token,
        resource="Document::doc123",
        action="read",
        secret="test-secret"
    )
    assert decision.decision == "ALLOW"
```

#### 4. Compilation (`test_compiler.py`)

Tests for Cedar policy compilation:

```python
def test_compile_simple_policy():
    """Test compiling basic Cedar policy"""
    policy = """
    permit(
        principal == User::"alice",
        action == Action::"read",
        resource == Document::"doc123"
    );
    """
    result = compile_policy(policy)
    assert result.principal == "User::alice"
    assert "Document:doc123:read" in result.scopes

def test_compile_wildcard_policy():
    """Test compiling policy with wildcards"""
    policy = """
    permit(
        principal == User::"alice",
        action == Action::"read",
        resource is Document
    );
    """
    result = compile_policy(policy)
    assert "Document:*:read" in result.scopes

def test_compile_multiple_actions():
    """Test compiling policy with multiple actions"""
    policy = """
    permit(
        principal == User::"alice",
        action in [Action::"read", Action::"write"],
        resource == Document::"doc123"
    );
    """
    result = compile_policy(policy)
    assert "Document:doc123:read" in result.scopes
    assert "Document:doc123:write" in result.scopes
```

#### 5. Scope Operations (`test_scope.py`)

Tests for scope parsing and subset checking:

```python
def test_parse_scope():
    """Test scope string parsing"""
    scope = parse_scope("Document:doc123:read")
    assert scope.resource_type == "Document"
    assert scope.resource_id == "doc123"
    assert scope.action == "read"

def test_is_subset():
    """Test subset checking logic"""
    specific = parse_scope("Document:doc123:read")
    wildcard = parse_scope("Document:*:read")
    assert is_subset(specific, wildcard)
    assert not is_subset(wildcard, specific)

def test_scopes_cover_request():
    """Test if granted scopes cover request"""
    requested = parse_scope("Document:doc123:read")
    granted = [
        parse_scope("Document:*:read"),
        parse_scope("Document:doc456:write")
    ]
    assert scopes_cover_request(requested, granted)
```

## Integration Tests

Integration tests require **deployed AWS infrastructure** and test the full stack.

### Setup

```bash
# Deploy infrastructure first
./scripts/deploy.sh

# Set environment variables (or use .env file)
export POLICY_STORE_ID="..."
export RAJA_API_URL="..."
export AWS_REGION="us-east-1"
export PRINCIPAL_TABLE="..."

# Seed test data
./poe seed-test-data

# Run integration tests
./poe test-integration
```

### Test Coverage

#### 1. End-to-End (`test_end_to_end.py`)

Full authorization flow:

```python
@pytest.mark.integration
def test_full_authorization_flow(api_url, policy_store_id):
    """Test complete flow: compile → token → enforce"""
    # 1. Load policy to AVP
    # 2. Trigger compiler Lambda
    # 3. Request token from Token Service
    # 4. Check authorization via Enforcer
    # 5. Verify result
```

#### 2. Policy Store (`test_policy_store.py`)

AVP policy store operations:

```python
@pytest.mark.integration
def test_create_policy(policy_store_id):
    """Test creating policy in AVP"""

@pytest.mark.integration
def test_update_policy(policy_store_id):
    """Test updating existing policy"""

@pytest.mark.integration
def test_list_policies(policy_store_id):
    """Test listing all policies"""
```

#### 3. Token Service (`test_token_service.py`)

Token service Lambda tests:

```python
@pytest.mark.integration
def test_request_token(api_url):
    """Test requesting token via API"""
    response = requests.post(
        f"{api_url}/token",
        json={"principal": "User::alice"}
    )
    assert response.status_code == 200
    assert "token" in response.json()
```

#### 4. Enforcement Service (`test_enforcement_service.py`)

Enforcer Lambda tests:

```python
@pytest.mark.integration
def test_enforce_via_api(api_url, valid_token):
    """Test authorization check via API"""
    response = requests.post(
        f"{api_url}/authorize",
        json={
            "token": valid_token,
            "resource": "Document::doc123",
            "action": "read"
        }
    )
    assert response.status_code == 200
    assert response.json()["decision"] in ["ALLOW", "DENY"]
```

## Property-Based Tests

Property-based tests use the `hypothesis` library to validate core invariants across many randomly-generated inputs.

### Core Properties

#### 1. Compilation Determinism (`test_compilation.py`)

Same policy always produces same scopes:

```python
@pytest.mark.hypothesis
@given(st.text(min_size=1))
def test_compilation_determinism(policy_string):
    """Same policy compiled twice produces identical scopes"""
    result1 = compile_policy(policy_string)
    result2 = compile_policy(policy_string)
    assert result1.scopes == result2.scopes
```

#### 2. Token Determinism (`test_determinism.py`)

Same inputs always produce same tokens (excluding timestamps):

```python
@pytest.mark.hypothesis
@given(
    principal=st.text(min_size=1),
    scopes=st.lists(st.text(min_size=1))
)
def test_token_determinism(principal, scopes):
    """Same inputs produce same token payload"""
    token1 = create_token(principal, scopes, "secret")
    token2 = create_token(principal, scopes, "secret")

    decoded1 = decode_token(token1, "secret")
    decoded2 = decode_token(token2, "secret")

    assert decoded1.principal == decoded2.principal
    assert decoded1.scopes == decoded2.scopes
```

#### 3. Fail-Closed Semantics (`test_fail_closed.py`)

Unknown/invalid requests always DENY:

```python
@pytest.mark.hypothesis
@given(
    requested=st.text(min_size=1),
    granted=st.lists(st.text(min_size=1))
)
def test_fail_closed(requested, granted):
    """Enforcement defaults to DENY for ambiguous cases"""
    # Create token with granted scopes
    token = create_token("User::test", granted, "secret")

    # Request random scope
    try:
        decision = enforce(token, f"Resource::{requested}", "action", "secret")
        # If no error, decision must be ALLOW or DENY (never ambiguous)
        assert decision.decision in ["ALLOW", "DENY"]
    except Exception:
        # Errors are acceptable (fail-closed)
        pass
```

#### 4. Output Transparency (`test_transparency.py`)

Every decision includes explanation:

```python
@pytest.mark.hypothesis
@given(
    principal=st.text(min_size=1),
    scopes=st.lists(st.text(min_size=1)),
    resource=st.text(min_size=1),
    action=st.text(min_size=1)
)
def test_output_transparency(principal, scopes, resource, action):
    """All decisions include reason and relevant scopes"""
    token = create_token(principal, scopes, "secret")
    decision = enforce(token, f"Resource::{resource}", action, "secret")

    assert decision.reason is not None
    assert len(decision.reason) > 0
    assert decision.requested_scope is not None
    assert decision.granted_scopes is not None
```

## Test Fixtures

Shared fixtures in `conftest.py`:

```python
@pytest.fixture
def test_secret() -> str:
    """JWT signing secret for tests"""
    return "test-secret-key-for-jwt"

@pytest.fixture
def sample_policy() -> str:
    """Sample Cedar policy for tests"""
    return """
    permit(
        principal == User::"alice",
        action == Action::"read",
        resource == Document::"doc123"
    );
    """

@pytest.fixture
def sample_token(test_secret) -> str:
    """Sample JWT token for tests"""
    return create_token(
        principal="User::alice",
        scopes=["Document:doc123:read"],
        secret=test_secret
    )
```

Integration tests read configuration from environment variables:

- `RAJA_API_URL` for the API Gateway base URL
- `POLICY_STORE_ID` for the Verified Permissions policy store

## Running Tests

### Quick Commands

```bash
# All tests
./poe test

# Unit tests only (fast)
./poe test-unit

# Integration tests only
./poe test-integration

# Hypothesis tests only
./poe test-hypothesis

# With coverage report
./poe test-cov

# Watch mode (re-run on file changes)
./poe test-watch
```

### Advanced Usage

```bash
# Run specific test file
pytest tests/unit/test_token.py

# Run specific test function
pytest tests/unit/test_token.py::test_create_token

# Run with verbose output
pytest -v

# Run with debug output
pytest -s

# Run in parallel (requires pytest-xdist)
pytest -n auto

# Generate HTML coverage report
pytest --cov --cov-report=html
open htmlcov/index.html
```

## Coverage Requirements

- **Overall:** Minimum 80% code coverage
- **Unit tests:** Should cover all core library functions
- **Integration tests:** Should cover all Lambda handlers
- **Hypothesis tests:** Should validate all core properties

### Checking Coverage

```bash
# Generate coverage report
./poe test-cov

# View coverage report
# Text report printed to terminal
# HTML report in htmlcov/index.html
```

## CI/CD Integration

### GitHub Actions Workflows

1. **CI Workflow** (`.github/workflows/ci.yml`)

   ```yaml
   - Format check (ruff)
   - Lint (ruff)
   - Type check (mypy)
   - Unit tests (pytest)
   - Build package
   ```

2. **Integration Workflow** (`.github/workflows/integration.yml`)

   ```yaml
   - Deploy infrastructure
   - Load test policies
   - Run integration tests
   - Tear down infrastructure
   ```

### Local Pre-commit Checks

```bash
# Run all checks before committing
./poe check-all

# This runs:
# - ./poe format (ruff format)
# - ./poe lint (ruff check)
# - ./poe typecheck (mypy)
```

## Best Practices

1. **Isolation:** Unit tests should not depend on external services
2. **Determinism:** Tests should produce consistent results
3. **Fast feedback:** Unit tests should run in < 5 seconds
4. **Clear assertions:** Use descriptive assertion messages
5. **Fixtures over mocks:** Prefer real objects with test data over mocks
6. **Property-based:** Use hypothesis for invariants
7. **Integration coverage:** Test all API endpoints and Lambda handlers

## Debugging Tests

```bash
# Run with pdb debugger
pytest --pdb

# Drop into debugger on first failure
pytest -x --pdb

# Show local variables in tracebacks
pytest -l

# Disable capturing for print debugging
pytest -s
```

## Future Enhancements

- **Performance tests** - Load testing with Locust
- **Security tests** - Penetration testing scenarios
- **Chaos tests** - Fault injection with chaos engineering
- **Contract tests** - Pact tests for API contracts
- **Mutation tests** - Mutmut for test quality
