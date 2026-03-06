# RAJA MVP Specification
## Resource Authorization JWT Authority - Minimal Viable Product

## Purpose
Validate the Software-Defined Authorization (SDA) hypothesis: that authorization can be treated as a **compiled discipline** rather than runtime interpretation.

---

## Core Hypothesis

Authorization should be:
1. **Compiled at control plane:** Cedar policies → JWT tokens with explicit scopes
2. **Mechanically enforced at data plane:** Pure subset checking without policy evaluation
3. **Deterministic and unambiguous:** Same inputs always produce same outputs

---

## Minimal Components

### 1. Control Plane: Policy Compiler

**Input:** Simple Cedar-like policy
```cedar
permit(
  principal == User::"alice",
  action == Action::"read",
  resource == Document::"doc123"
);
```

**Output:** JWT token with canonical scopes
```json
{
  "sub": "alice",
  "scopes": [
    "Document:doc123:read"
  ],
  "iat": 1704067200,
  "exp": 1704153600
}
```

**Key Properties:**
- Each policy statement compiles to one or more scope strings
- Scope format: `{ResourceType}:{ResourceId}:{Action}`
- No ambiguity: scope strings have explicit semantics
- No inference: exactly what's granted is in the token

---

### 2. Data Plane: Enforcement Engine

**Input:** Token + Authorization Request
```python
token = {
    "scopes": ["Document:doc123:read", "Document:doc123:write"]
}

request = {
    "resource_type": "Document",
    "resource_id": "doc123",
    "action": "read"
}
```

**Process:** Subset checking
```python
requested_scope = f"{request.resource_type}:{request.resource_id}:{request.action}"
decision = requested_scope in token["scopes"]
# "Document:doc123:read" in scopes → True → ALLOW
```

**Output:** Binary decision (ALLOW/DENY)

**Key Properties:**
- **Zero policy evaluation:** Cedar is absent from data plane
- **Pure subset checking:** `request ⊆ authority`
- **Fail-closed:** Unknown/ambiguous requests → DENY
- **Deterministic:** No context lookups, no interpretation

---

## Test Scenarios

### Scenario 1: Exact Match
```python
token.scopes = ["Document:doc123:read"]
request = {"resource_type": "Document", "resource_id": "doc123", "action": "read"}
expected = ALLOW
```

### Scenario 2: Missing Permission
```python
token.scopes = ["Document:doc123:read"]
request = {"resource_type": "Document", "resource_id": "doc123", "action": "write"}
expected = DENY
```

### Scenario 3: Different Resource
```python
token.scopes = ["Document:doc123:read"]
request = {"resource_type": "Document", "resource_id": "doc456", "action": "read"}
expected = DENY
```

### Scenario 4: Expired Token
```python
token.scopes = ["Document:doc123:read"]
token.exp = 1000000000  # Past timestamp
request = {"resource_type": "Document", "resource_id": "doc123", "action": "read"}
expected = DENY (token expired)
```

### Scenario 5: Multiple Scopes
```python
token.scopes = ["Document:doc123:read", "Document:doc123:write", "Document:doc456:read"]
request = {"resource_type": "Document", "resource_id": "doc456", "action": "read"}
expected = ALLOW
```

---

## Success Criteria

The MVP validates the RAJA hypothesis if:

✅ **Compilation:** Cedar policies → JWT tokens with explicit scopes
✅ **Enforcement:** Pure subset checking without policy evaluation
✅ **Determinism:** Same token + request = same result always
✅ **Fail-closed:** Unknown requests automatically denied
✅ **Transparency:** Token inspection reveals exact authorities
✅ **Expiration:** Expired tokens mechanically rejected

---

## What's NOT in MVP

❌ AWS AVP service integration
❌ Complex Cedar features (conditions, hierarchies, variables)
❌ Token revocation
❌ HTTP API service
❌ Database/persistence
❌ Multi-tenancy
❌ Policy validation/linting
❌ Token refresh

---

## Implementation Scope

### Files to Create:

```
src/raja/
├── models.py           # Scope, Token, Request, Decision dataclasses
├── control_plane.py    # compile_policy(cedar_policy) → JWT
├── data_plane.py       # enforce(token, request) → Decision
└── __init__.py         # Public API exports

tests/
└── test_mvp.py         # All 6 test scenarios above
```

### Dependencies:
- `PyJWT` - JWT token creation and validation
- `pydantic` - Data validation and models
- `pytest` - Testing framework

---

## Example Usage

```python
from raja import compile_policy, enforce, AuthRequest

# Control Plane: Compile policy to token
policy = """
permit(
  principal == User::"alice",
  action == Action::"read",
  resource == Document::"doc123"
);
"""
token = compile_policy(policy, subject="alice", ttl_seconds=3600)

# Data Plane: Enforce authorization
request = AuthRequest(
    resource_type="Document",
    resource_id="doc123",
    action="read"
)
decision = enforce(token, request)
assert decision.allowed == True
```

---

## Validation

This MVP tests the fundamental SDA claim: **authorization as compilation, not interpretation.**

If we can demonstrate that:
- Policies compile to bearer tokens
- Enforcement is mechanical subset checking
- System is deterministic and fail-closed

Then we've validated the core RAJA hypothesis for building production authorization systems.
