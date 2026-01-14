# RAJA
![CI](https://github.com/quiltdata/raja/workflows/CI/badge.svg)
![Integration Tests](https://github.com/quiltdata/raja/workflows/Integration%20Tests/badge.svg)
![Coverage](https://codecov.io/gh/quiltdata/raja/branch/main/graph/badge.svg)

**Resource Authorization JWT Authority** - Compile Cedar policies into JWT tokens for deterministic authorization.

## What is RAJA?

RAJA compiles Cedar authorization policies into JWT tokens with explicit scopes. This means:

- Authorization decisions are **deterministic** (same token + request = same result)
- Tokens are **transparent** (you can see exactly what permissions are granted)
- Enforcement is **fast** (simple scope checking, no policy evaluation)

## Quick Start

### Installation

```bash
git clone https://github.com/quiltdata/raja.git
cd raja
uv sync
```

### Deploy to AWS

```bash
# Deploy infrastructure
poe cdk-deploy --all

# Load Cedar policies
python scripts/load_policies.py

# Compile policies to scopes
python scripts/invoke_compiler.py
```

### Try the Web Interface

After deployment, the CDK outputs will include a CloudFront URL. Open it in your browser to:

1. Request JWT tokens for different users (alice, bob, admin)
2. Test authorization decisions
3. Inspect token claims

See [web/README.md](web/README.md) for details.

## How It Works

```text
Cedar Policies → Compiler → JWT Scopes → Enforcer → ALLOW/DENY
```

1. **Write Cedar policies** that define who can do what
2. **Compiler** converts policies into scope strings (e.g., `Document:doc123:read`)
3. **Token Service** issues JWTs containing these scopes
4. **Enforcer** checks if requested scope is in the token

## API Endpoints

When deployed to AWS, RAJA provides:

**POST /token** - Issue a JWT token

```json
{"principal": "alice"}
→ {"token": "eyJ...", "scopes": ["Document:doc123:read"]}
```

**POST /authorize** - Check authorization

```json
{"token": "eyJ...", "request": {"resource_type": "Document", "resource_id": "doc123", "action": "read"}}
→ {"allowed": true, "reason": "Scope found in token"}
```

**GET /introspect** - Inspect token

```text
?token=eyJ...
→ {"claims": {"sub": "alice", "scopes": [...]}}
```

## Local Development

Use the Python library standalone (no AWS required):

```python
from raja import create_token, enforce

# Create token with scopes
token = create_token(
    subject="alice",
    scopes=["Document:doc123:read"],
    secret="your-secret"
)

# Check authorization
decision = enforce(
    token=token,
    resource="Document::doc123",
    action="read",
    secret="your-secret"
)
print(decision.allowed)  # True
```

### Run Tests

```bash
poe test-unit      # Unit tests (no AWS)
poe test           # All tests
poe check-all      # Format, lint, typecheck
```

## Scope Format

Scopes follow the pattern: `{ResourceType}:{ResourceId}:{Action}`

Examples:

- `Document:doc123:read` - Read document doc123
- `Document:*:read` - Read all documents
- `*:*:*` - Full admin access

## Project Structure

```text
raja/
├── src/raja/           # Core Python library
├── lambda_handlers/    # AWS Lambda functions
├── infra/             # CDK infrastructure
├── web/               # Web demo interface
├── policies/          # Sample Cedar policies
└── tests/             # Test suite
```

## Documentation

- **[CLAUDE.md](CLAUDE.md)** - Developer guide and architecture
- **[web/README.md](web/README.md)** - Web interface documentation
- **[specs/](specs/)** - Design specifications
- **Module READMEs** - See CLAUDE.md files in subdirectories

## Contributing

See [CLAUDE.md](CLAUDE.md) for development guidelines.

## License

[License information to be added]
