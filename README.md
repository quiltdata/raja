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

### Deploy to AWS (Control Plane)

```bash
# Deploy infrastructure
./poe deploy

# Load Cedar policies
python scripts/load_policies.py

# Compile policies to scopes
export RAJA_API_URL="https://your-api.execute-api.us-east-1.amazonaws.com/prod"
python scripts/invoke_compiler.py
```

### Control Plane UI

After deployment, open the API Gateway URL in your browser. The root path (`/`) renders a
simple admin UI with live data from `/principals`, `/policies`, and `/audit`.

## How It Works

```text
Cedar Policies → Compiler → JWT Scopes → Library Enforcement
```

1. **Write Cedar policies** that define who can do what
2. **Compiler** converts policies into scope strings (e.g., `Document:doc123:read`)
3. **Token Service** issues JWTs containing these scopes
4. **Applications** validate tokens and check scopes locally

## API Endpoints

When deployed to AWS, RAJA provides:

**POST /compile** - Compile Cedar policies into scopes

```json
{}
→ {"message": "Policies compiled successfully", "policies_compiled": 3}
```

**POST /token** - Issue a JWT token

```json
{"principal": "alice"}
→ {"token": "eyJ...", "scopes": ["S3Object:analytics-data/*:s3:GetObject", "S3Bucket:analytics-data:s3:ListBucket"]}
```

**GET /principals** - List principals and their scopes

```text
→ {"principals": [{"principal": "alice", "scopes": [...]}]}

**GET /policies** - List Cedar policies

```json
→ {"policies": [{"policyId": "..."}]}
```

**GET /audit** - View audit log entries

```
Query params:
  principal=<principal>
  action=<action>
  resource=<resource>
  start_time=<epoch-seconds>
  end_time=<epoch-seconds>
  limit=<1-200>
  next_token=<pagination-token>

Response fields include: timestamp, principal, action, resource, decision,
policy_store_id, request_id.

```

## Local Development

Use the Python library standalone (no AWS required):

```python
from raja import AuthRequest, create_token, enforce

# Create token with S3 scopes
token = create_token(
    subject="alice",
    scopes=[
        "S3Object:analytics-data/*:s3:GetObject",
        "S3Bucket:analytics-data:s3:ListBucket"
    ],
    secret="your-secret"
)

# Check authorization for S3 GetObject
decision = enforce(
    token_str=token,
    request=AuthRequest(
        resource_type="S3Object",
        resource_id="analytics-data/reports/2024.csv",
        action="s3:GetObject"
    ),
    secret="your-secret"
)
print(decision.allowed)  # True
```

### Run Tests

```bash
./poe test-unit    # Unit tests (no AWS)
./poe test         # All tests
./poe check        # Format, lint, typecheck
```

### Demo RAJEE Envoy S3 Proxy

To demonstrate RAJEE's Envoy proxy correctly routing S3 operations:

```bash
./poe demo
```

This runs verbose integration tests showing:

- S3 operations (PUT, GET, DELETE, LIST) proxied through Envoy
- Host header rewriting (Envoy endpoint → s3.amazonaws.com)
- Multiple S3 API operations (GetObject, ListObjects, GetObjectAttributes, versioning)
- Timing metrics for each operation
- Complete request/response verification

## Scope Format

Scopes follow the pattern: `{ResourceType}:{ResourceId}:{Action}`

Examples:

- `S3Object:analytics-data/reports/2024.csv:s3:GetObject` - Read specific S3 object
- `S3Object:analytics-data/*:s3:GetObject` - Read all objects in bucket
- `S3Bucket:analytics-data:s3:ListBucket` - List bucket contents
- `*:*:*` - Full admin access

## Project Structure

```text
raja/
├── src/raja/           # Core Python library
├── lambda_handlers/    # AWS Lambda handlers
├── infra/             # CDK infrastructure
├── policies/          # Sample Cedar policies
└── tests/             # Test suite
```

## Documentation

- **[CLAUDE.md](CLAUDE.md)** - Developer guide and architecture
- **[specs/](specs/)** - Design specifications
- **Module READMEs** - See CLAUDE.md files in subdirectories

## Contributing

See [CLAUDE.md](CLAUDE.md) for development guidelines.

## License

[License information to be added]
