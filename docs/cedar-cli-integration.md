# Cedar CLI Integration

## Overview

RAJA integrates with the official Cedar policy language tooling to provide robust policy parsing, validation, and compilation. This integration replaces the legacy regex-based parser with production-grade Cedar validation.

## Features

### Phase 1: Basic Cedar CLI Integration

**Feature Flag:** `RAJA_USE_CEDAR_CLI`

- **Enabled (default):** Uses Cedar Rust parser via subprocess
- **Disabled:** Falls back to legacy regex-based parser

```bash
# Enable Cedar CLI (default if cargo/cedar is available)
export RAJA_USE_CEDAR_CLI=true

# Disable Cedar CLI (use legacy parser)
export RAJA_USE_CEDAR_CLI=false
```

**Requirements:**

- Rust toolchain (cargo) OR
- Pre-built `cedar_parse` binary (set via `CEDAR_PARSE_BIN` env var)

**Graceful Degradation:**

If Cedar CLI is unavailable, RAJA automatically falls back to the legacy parser with a warning.

### Phase 2: Schema Validation

Cedar schemas define valid entities, actions, and constraints for policies.

**Loading Schema:**

```python
from raja.cedar.schema import load_cedar_schema

schema = load_cedar_schema("path/to/schema.cedar", validate=True)
```

**Validating Policies Against Schema:**

```python
from raja.cedar.schema import validate_policy_against_schema

policy = '''
permit(
    principal == User::"alice",
    action == Action::"read",
    resource == Document::"doc123"
);
'''

validate_policy_against_schema(policy, "path/to/schema.cedar")
```

**Schema Format (Cedar):**

```cedar
// Entity declarations
entity User {}
entity Document {}
entity S3Bucket {}
entity S3Object in [S3Bucket] {}

// Action declarations
action "read" appliesTo {
    principal: [User],
    resource: [Document]
};

action "s3:GetObject" appliesTo {
    principal: [User],
    resource: [S3Object]
};
```

**Benefits:**

- Catches invalid entity references at compile time
- Validates action-resource compatibility
- Ensures principal types match schema
- Prevents deployment of malformed policies

### Phase 3: Forbid Policy Support

Forbid policies enable deny-by-default security models.

**Basic Forbid:**

```python
from raja.compiler import compile_policies

policies = [
    # Permit read and write
    '''permit(
        principal == User::"alice",
        action in [Action::"read", Action::"write", Action::"delete"],
        resource == Document::"doc123"
    );''',

    # Forbid delete
    '''forbid(
        principal == User::"alice",
        action == Action::"delete",
        resource == Document::"doc123"
    );'''
]

# Compile with forbid handling
result = compile_policies(policies, handle_forbids=True)
# Result: alice can read and write, but NOT delete
```

**Forbid Precedence:**

- Forbid always takes precedence over permit
- Order of policy definitions doesn't matter
- Forbidden scopes are excluded from final scope list

**Example: Prevent Dangerous Actions:**

```python
policies = [
    # Grant broad S3 access
    '''permit(
        principal == User::"alice",
        action in [Action::"s3:GetObject", Action::"s3:PutObject", Action::"s3:DeleteObject"],
        resource == S3Object::"data.csv"
    ) when {
        resource in S3Bucket::"my-bucket"
    };''',

    # Forbid deletion (safety guard)
    '''forbid(
        principal == User::"alice",
        action == Action::"s3:DeleteObject",
        resource == S3Object::"data.csv"
    ) when {
        resource in S3Bucket::"my-bucket"
    };'''
]

result = compile_policies(policies, handle_forbids=True)
# Result: alice can read and write, but cannot delete
```

### Phase 4: Advanced Features

#### Wildcard Pattern Matching

**Scope Wildcards:**

```python
from raja.scope import scope_matches, parse_scope

# Resource ID wildcard
granted = parse_scope("Document:*:read")
requested = parse_scope("Document:doc123:read")
assert scope_matches(requested, granted)  # True

# Action wildcard
granted = parse_scope("S3Object:obj123:s3:*")
requested = parse_scope("S3Object:obj123:s3:GetObject")
assert scope_matches(requested, granted)  # True

# Full wildcard (admin scope)
granted = parse_scope("*:*:*")
requested = parse_scope("Document:doc123:write")
assert scope_matches(requested, granted)  # True
```

**Action Prefix Wildcards:**

```python
# Match all s3 actions
granted = parse_scope("S3Object:obj123:s3:*")

# Matches:
# - s3:GetObject
# - s3:PutObject
# - s3:DeleteObject
# - etc.
```

#### Wildcard Expansion

Expand wildcard patterns to concrete scopes:

```python
from raja.scope import expand_wildcard_scope

# Expand resource type wildcard
scopes = expand_wildcard_scope(
    "*:doc123:read",
    resource_types=["Document", "File", "Image"]
)
# Result: ["Document:doc123:read", "File:doc123:read", "Image:doc123:read"]

# Expand action wildcard
scopes = expand_wildcard_scope(
    "Document:doc123:s3:*",
    actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
)
# Result: ["Document:doc123:s3:GetObject", "Document:doc123:s3:PutObject", "Document:doc123:s3:DeleteObject"]
```

#### Scope Filtering

Filter scopes by inclusion/exclusion patterns:

```python
from raja.scope import filter_scopes_by_pattern

scopes = [
    "S3Bucket:bucket-a:s3:GetObject",
    "S3Bucket:bucket-a:s3:PutObject",
    "S3Bucket:bucket-a:s3:DeleteObject",
    "S3Bucket:bucket-b:s3:GetObject",
]

# Exclude delete operations
filtered = filter_scopes_by_pattern(
    scopes,
    exclude_patterns=["*:*:s3:DeleteObject"]
)
# Result: All scopes except DeleteObject
```

#### Policy Template Instantiation

Create reusable policy templates with variable substitution:

```python
from raja.compiler import instantiate_policy_template

template = '''
permit(
    principal == User::"{{user}}",
    action == Action::"{{action}}",
    resource == S3Bucket::"{{bucket}}"
);
'''

# Instantiate template with variables
result = instantiate_policy_template(
    template,
    variables={
        "user": "alice",
        "action": "s3:ListBucket",
        "bucket": "my-bucket"
    }
)
# Result: {"alice": ["S3Bucket:my-bucket:s3:ListBucket"]}
```

**Supported Template Variables:**

- `{{user}}` - User identifier
- `{{principal}}` - Principal identifier
- `{{action}}` - Action identifier
- `{{resource}}` - Resource identifier
- `{{bucket}}` - S3 bucket identifier
- Custom variables (alphanumeric + underscore)

**Multi-Resource Templates:**

```python
template = '''
permit(
    principal == User::"{{user}}",
    action == Action::"s3:GetObject",
    resource == S3Object::"{{key}}"
) when {
    resource in S3Bucket::"{{bucket}}"
};
'''

result = instantiate_policy_template(
    template,
    variables={
        "user": "bob",
        "key": "data/report.csv",
        "bucket": "analytics"
    }
)
# Result: {"bob": ["S3Object:analytics/data/report.csv:s3:GetObject"]}
```

## Installation

### Option 1: Rust Toolchain

Install Rust and Cargo:

```bash
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh
```

### Option 2: Pre-built Binary

Build the Cedar parser once and set environment variable:

```bash
cd tools/cedar-validate
cargo build --release --bin cedar_parse

# Set environment variable
export CEDAR_PARSE_BIN=/path/to/raja/tools/cedar-validate/target/release/cedar_parse
```

### Option 3: Docker

Use Docker image with Rust pre-installed:

```dockerfile
FROM rust:1.75
COPY . /app
WORKDIR /app
RUN cargo build --release --manifest-path tools/cedar-validate/Cargo.toml
ENV CEDAR_PARSE_BIN=/app/tools/cedar-validate/target/release/cedar_parse
```

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `RAJA_USE_CEDAR_CLI` | Enable Cedar CLI parsing | `true` (if available) |
| `CEDAR_PARSE_BIN` | Path to pre-built cedar_parse binary | None |
| `CEDAR_VALIDATE_BIN` | Path to pre-built cedar_validate binary | None |

## Testing

### Unit Tests

```bash
# Run all Cedar-related unit tests
pytest tests/unit/test_cedar_parser.py
pytest tests/unit/test_cedar_schema_validation.py
pytest tests/unit/test_compiler_forbid.py
pytest tests/unit/test_compiler_templates.py
pytest tests/unit/test_scope_wildcards.py

# Run with Rust tooling check
./poe test
```

### Integration Tests

```bash
# Deploy infrastructure and run integration tests
./poe deploy
./poe test-integration
```

### CI/CD

GitHub Actions automatically:

1. Installs Rust toolchain
2. Runs unit tests with Cedar CLI
3. Validates schema files
4. Tests policy compilation

## Performance

### Cedar CLI Overhead

- **Parsing:** ~10-50ms per policy (subprocess overhead)
- **Validation:** ~50-100ms per policy with schema
- **Caching:** Results cached in DynamoDB for production use

### Optimization Strategies

1. **Batch Processing:** Compile multiple policies in parallel
2. **Pre-compilation:** Compile policies during deployment, not runtime
3. **Binary Distribution:** Use pre-built binary to avoid Cargo overhead

## Migration from Legacy Parser

### Automatic Fallback

Legacy parser is used automatically if Cedar CLI is unavailable:

```python
from raja.cedar.parser import parse_policy

# Automatically chooses Cedar CLI or legacy parser
parsed = parse_policy(policy_str)
```

### Feature Comparison

| Feature | Cedar CLI | Legacy Parser |
|---------|-----------|---------------|
| Basic parsing | ✅ | ✅ |
| Forbid effect | ✅ | ✅ (recognized) |
| Schema validation | ✅ | ❌ |
| Syntax validation | ✅ | Limited |
| Complex conditions | ✅ | Limited |
| Error messages | Detailed | Basic |

### Breaking Changes

**None.** Cedar CLI integration is backward compatible.

## Troubleshooting

### "cargo is required to parse Cedar policies"

**Solution:** Install Rust toolchain or set `CEDAR_PARSE_BIN` environment variable.

### "falling back to legacy Cedar parsing"

**Cause:** Cedar CLI unavailable or runtime error.

**Action:** Check Rust installation and Cedar binary location.

### Schema validation errors

**Solution:** Validate schema file syntax:

```bash
cd tools/cedar-validate
cargo run --bin cedar_validate -- schema /path/to/schema.cedar
```

### Policy syntax errors

**Solution:** Use Cedar CLI for detailed error messages:

```bash
echo 'permit(...)' | cargo run --bin cedar_parse
```

## Best Practices

1. **Always validate against schema:** Catch errors early
2. **Use forbid for security:** Explicit denials prevent privilege escalation
3. **Prefer specific scopes:** Wildcards should be used sparingly
4. **Template common patterns:** Reduce policy duplication
5. **Test policy compilation:** Verify policies compile before deployment

## Limitations

### Current Limitations (Phase 1-4)

1. **when/unless clauses:** Only `resource in` conditions supported
2. **Context variables:** Not supported (e.g., `context.ip`, `context.time`)
3. **Complex boolean logic:** Only OR combinations of `resource in` supported
4. **Template variables:** Limited to predefined set (user, bucket, etc.)

### Future Enhancements

1. **Full condition support:** Context variables, complex boolean logic
2. **Policy templates API:** REST API for template instantiation
3. **Policy conflict detection:** Warn about overlapping/redundant policies
4. **Policy optimization:** Minimize scope sets automatically

## Resources

- [Cedar Policy Language](https://www.cedarpolicy.com/)
- [Cedar Rust SDK](https://docs.rs/cedar-policy/)
- [Amazon Verified Permissions](https://aws.amazon.com/verified-permissions/)
- [RAJA Repository](https://github.com/quiltdata/raja)

## Support

For issues or questions:

1. Check existing tests: `tests/unit/test_cedar_*.py`
2. Review specifications: `specs/3-schema/09-cedar-next.md`
3. Open GitHub issue with reproducible example
