# RAJA Core Library

## Overview

The `raja` Python library provides the core authorization logic for the RAJA system. It is **pure Python** with no AWS dependencies, making it suitable for standalone use or integration with AWS infrastructure.

## Module Structure

```
src/raja/
├── __init__.py           # Public API exports
├── models.py             # Data models (Pydantic)
├── token.py              # JWT token operations
├── enforcer.py           # Authorization enforcement
├── compiler.py           # Policy compilation
├── scope.py              # Scope operations
└── cedar/                # Cedar policy handling
    ├── __init__.py
    ├── parser.py         # Parse Cedar policy strings
    └── schema.py         # Cedar schema validation
```

## Public API

The library exports a clean, minimal API:

```python
from raja import (
    # Core functions
    compile_policy,      # Cedar → scopes
    create_token,        # Create JWT with scopes
    decode_token,        # Decode and validate JWT
    enforce,             # Check authorization

    # Models
    Scope,              # Scope data model
    AuthRequest,        # Authorization request
    Decision,           # Authorization decision
    Token,              # Token model
    CompilationResult,  # Compilation result
    CedarPolicy,        # Cedar policy model
)
```

## Core Components

### 1. Models (`models.py`)

Pydantic models for type-safe data handling:

#### Scope
```python
class Scope(BaseModel):
    resource_type: str    # e.g., "Document"
    resource_id: str      # e.g., "doc123" or "*"
    action: str          # e.g., "read", "write", "*"

    @classmethod
    def from_string(cls, s: str) -> "Scope":
        """Parse scope from string format: ResourceType:ResourceId:Action"""

    def to_string(self) -> str:
        """Convert to string format: ResourceType:ResourceId:Action"""

    def is_subset_of(self, other: "Scope") -> bool:
        """Check if this scope is a subset of another scope"""
```

#### AuthRequest
```python
class AuthRequest(BaseModel):
    principal: str       # e.g., "User::alice"
    action: str         # e.g., "read"
    resource: str       # e.g., "Document::doc123"
```

#### Decision
```python
class Decision(BaseModel):
    decision: Literal["ALLOW", "DENY"]
    reason: str
    requested_scope: Optional[str]
    granted_scopes: Optional[list[str]]
```

#### Token
```python
class Token(BaseModel):
    principal: str
    scopes: list[str]
    iat: Optional[int]    # Issued at
    exp: Optional[int]    # Expiration
    token: Optional[str]  # Encoded JWT
```

#### CompilationResult
```python
class CompilationResult(BaseModel):
    policy_id: str
    principal: str
    scopes: list[str]
    metadata: dict[str, Any]
```

#### CedarPolicy
```python
class CedarPolicy(BaseModel):
    id: str
    effect: Literal["permit", "forbid"]
    principal: dict[str, Any]
    action: dict[str, Any]
    resource: dict[str, Any]
    conditions: Optional[list[dict[str, Any]]]
```

### 2. Scope Operations (`scope.py`)

Core scope manipulation:

```python
def parse_scope(scope_str: str) -> Scope:
    """Parse scope string into Scope model"""

def format_scope(scope: Scope) -> str:
    """Format Scope model into string"""

def is_subset(requested: Scope, granted: Scope) -> bool:
    """Check if requested scope is subset of granted scope

    Examples:
        Document:doc123:read ⊆ Document:*:read  # True
        Document:doc123:write ⊆ Document:doc123:*  # True
        Document:doc123:read ⊆ *:*:*  # True
        Document:doc456:read ⊆ Document:doc123:read  # False
    """

def scopes_cover_request(
    requested: Scope,
    granted_scopes: list[Scope]
) -> bool:
    """Check if any granted scope covers the requested scope"""
```

### 3. Token Operations (`token.py`)

JWT token creation and validation:

```python
def create_token(
    principal: str,
    scopes: list[str],
    secret: str,
    expiration_minutes: int = 60
) -> str:
    """Create JWT token with scopes as claims

    Args:
        principal: Principal identifier (e.g., "User::alice")
        scopes: List of scope strings
        secret: JWT signing secret
        expiration_minutes: Token validity duration

    Returns:
        Encoded JWT token string
    """

def decode_token(token: str, secret: str) -> Token:
    """Decode and validate JWT token

    Args:
        token: Encoded JWT token string
        secret: JWT signing secret

    Returns:
        Token model with decoded claims

    Raises:
        ValueError: If token is invalid or expired
    """

def validate_token(token: str, secret: str) -> bool:
    """Check if token is valid and not expired"""
```

### 4. Enforcement (`enforcer.py`)

Pure subset checking - no policy evaluation:

```python
def enforce(
    token: str,
    resource: str,
    action: str,
    secret: str
) -> Decision:
    """Enforce authorization via subset checking

    Process:
    1. Decode and validate token
    2. Extract principal and scopes
    3. Construct requested scope from resource + action
    4. Check if requested scope is subset of any granted scope
    5. Return ALLOW or DENY with explanation

    Args:
        token: Encoded JWT token
        resource: Resource identifier (e.g., "Document::doc123")
        action: Action to perform (e.g., "read")
        secret: JWT signing secret

    Returns:
        Decision with ALLOW/DENY and explanation
    """

def check_authorization(
    requested_scope: Scope,
    granted_scopes: list[Scope]
) -> tuple[bool, str]:
    """Pure subset checking logic

    Returns:
        (is_allowed, reason)
    """
```

### 5. Policy Compilation (`compiler.py`)

Convert Cedar policies to scope strings:

```python
def compile_policy(policy: str) -> CompilationResult:
    """Compile Cedar policy to scope strings

    Process:
    1. Parse Cedar policy string
    2. Extract principal, action, resource
    3. Generate scope strings (ResourceType:ResourceId:Action)
    4. Return compilation result with metadata

    Args:
        policy: Cedar policy string

    Returns:
        CompilationResult with policy_id, principal, scopes

    Example:
        policy = '''
        permit(
            principal == User::"alice",
            action == Action::"read",
            resource == Document::"doc123"
        );
        '''

        result = compile_policy(policy)
        # result.principal = "User::alice"
        # result.scopes = ["Document:doc123:read"]
    """

def extract_scopes_from_policy(policy: CedarPolicy) -> list[str]:
    """Extract scope strings from parsed Cedar policy"""

def compile_multiple_policies(
    policies: list[str]
) -> dict[str, CompilationResult]:
    """Compile multiple policies, grouped by principal"""
```

### 6. Cedar Parser (`cedar/parser.py`)

Parse Cedar policy strings:

```python
def parse_cedar_policy(policy_str: str) -> CedarPolicy:
    """Parse Cedar policy string into structured format

    Handles:
    - permit/forbid effects
    - Principal specifications (== User::"alice" or in Group::"admins")
    - Action specifications (== Action::"read" or in ActionGroup::"write")
    - Resource specifications (== Document::"doc123" or is Document)
    - Conditions (when/unless clauses)

    Returns:
        CedarPolicy model
    """

def extract_principal(policy_str: str) -> dict[str, Any]:
    """Extract principal clause from Cedar policy"""

def extract_action(policy_str: str) -> dict[str, Any]:
    """Extract action clause from Cedar policy"""

def extract_resource(policy_str: str) -> dict[str, Any]:
    """Extract resource clause from Cedar policy"""

def extract_conditions(policy_str: str) -> list[dict[str, Any]]:
    """Extract when/unless conditions from Cedar policy"""
```

### 7. Cedar Schema (`cedar/schema.py`)

Validate Cedar schemas:

```python
def validate_schema(schema: dict[str, Any]) -> bool:
    """Validate Cedar schema structure

    Checks:
    - Entity type definitions
    - Action definitions
    - Attribute types
    - Schema version compatibility
    """

def extract_entity_types(schema: dict[str, Any]) -> list[str]:
    """Extract entity type names from schema"""

def extract_actions(schema: dict[str, Any]) -> list[str]:
    """Extract action names from schema"""
```

## Usage Examples

### Basic Flow

```python
from raja import compile_policy, create_token, enforce

# 1. Compile Cedar policy to scopes
policy = """
permit(
    principal == User::"alice",
    action == Action::"read",
    resource == Document::"doc123"
);
"""

result = compile_policy(policy)
# result.principal = "User::alice"
# result.scopes = ["Document:doc123:read"]

# 2. Create token with scopes
token = create_token(
    principal="User::alice",
    scopes=result.scopes,
    secret="my-secret-key",
    expiration_minutes=60
)

# 3. Enforce authorization
decision = enforce(
    token=token,
    resource="Document::doc123",
    action="read",
    secret="my-secret-key"
)

assert decision.decision == "ALLOW"
```

### Wildcard Scopes

```python
from raja import Scope

# Specific scope
specific = Scope.from_string("Document:doc123:read")

# Wildcard scopes
all_docs_read = Scope.from_string("Document:*:read")
doc_all_actions = Scope.from_string("Document:doc123:*")
admin = Scope.from_string("*:*:*")

# Subset checking
assert specific.is_subset_of(all_docs_read)     # True
assert specific.is_subset_of(doc_all_actions)   # True
assert specific.is_subset_of(admin)             # True
```

### Multiple Policies

```python
from raja import compile_multiple_policies

policies = [
    """
    permit(
        principal == User::"alice",
        action == Action::"read",
        resource == Document::"doc123"
    );
    """,
    """
    permit(
        principal == User::"alice",
        action == Action::"write",
        resource == Document::"doc456"
    );
    """
]

results = compile_multiple_policies(policies)
# results["User::alice"].scopes = [
#     "Document:doc123:read",
#     "Document:doc456:write"
# ]
```

## Design Patterns

### Fail-Closed
All enforcement defaults to DENY. Only explicit subset matches result in ALLOW.

### Immutable Tokens
Tokens are immutable once issued. Changes require new token issuance.

### Pure Functions
Core functions are pure - same inputs always produce same outputs.

### Type Safety
Full Pydantic models with strict validation. Mypy strict mode enabled.

## Testing

The core library has comprehensive unit tests in `tests/unit/`:

```bash
# Run all unit tests
./poe test-unit

# Run specific module tests
pytest tests/unit/test_models.py
pytest tests/unit/test_token.py
pytest tests/unit/test_enforcer.py
pytest tests/unit/test_compiler.py
pytest tests/unit/test_scope.py
```

## Dependencies

### Required
- **pydantic** (>=2.7.0) - Data validation
- **PyJWT** (>=2.8.0) - JWT operations

### Optional
None - core library is self-contained

## Error Handling

The library uses exceptions for error conditions:

- `ValueError` - Invalid input data (malformed scopes, invalid tokens)
- `jwt.ExpiredSignatureError` - Token has expired
- `jwt.InvalidTokenError` - Token is invalid or tampered
- `ValidationError` - Pydantic validation failures

All exceptions should be caught and handled by calling code.

## Performance Considerations

### Token Creation
- O(1) - Constant time JWT encoding
- Lightweight operation, suitable for high-frequency use

### Token Validation
- O(1) - Constant time JWT decoding and signature verification
- Minimal overhead

### Scope Checking
- O(n) - Linear in number of granted scopes
- Optimized for typical use cases (< 100 scopes per token)
- No policy evaluation - pure string/pattern matching

### Compilation
- O(m) - Linear in policy complexity
- One-time operation per policy
- Results cached in DynamoDB (when using AWS infrastructure)

## Best Practices

1. **Use typed models** - Leverage Pydantic for validation
2. **Keep secrets secure** - Never hardcode JWT secrets
3. **Set reasonable expirations** - Default 60 minutes, adjust as needed
4. **Minimize scope count** - Tokens with fewer scopes are faster to check
5. **Use wildcards sparingly** - More specific scopes are more secure
6. **Cache compiled scopes** - Don't recompile policies on every request
7. **Validate inputs** - Use Pydantic models to catch errors early

## Extension Points

The library is designed for extension:

1. **Custom scope formats** - Override `Scope.from_string()` / `to_string()`
2. **Alternative token formats** - Implement custom `create_token()` / `decode_token()`
3. **Additional policy languages** - Add parsers alongside Cedar parser
4. **Custom enforcement logic** - Extend `check_authorization()` for additional checks

## Future Enhancements

Potential future additions (not yet implemented):

- Scope hierarchies (parent/child relationships)
- Time-based scope restrictions
- Conditional scopes (context-aware)
- Scope templates (parameterized scopes)
- Policy validation (detect conflicts/redundancies)
