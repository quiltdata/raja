# RAJA Core Library

## Overview

The `raja` Python library provides the core authorization logic for the RAJA system. Pure Python with no AWS dependencies in the core modules.

## Module Structure

```text
src/raja/
├── __init__.py           # Public API exports
├── models.py             # Data models (Pydantic)
├── token.py              # JWT token operations
├── enforcer.py           # Authorization enforcement
├── scope.py              # Scope operations
├── exceptions.py         # Exception hierarchy
├── manifest.py           # Manifest handling
├── package_map.py        # PackageMap
├── quilt_uri.py          # Quilt URI parsing
├── cli.py                # CLI entrypoint
├── datazone/             # DataZone service integration
├── rajee/                # RAJEE grant conversion
├── rale/                 # RALE authorization logic
└── server/               # FastAPI control plane
```

## Public API

```python
from raja import (
    # Enforcement
    enforce,
    enforce_package_grant,
    enforce_translation_grant,
    enforce_with_routing,

    # Token operations
    create_token,
    create_token_with_grants,
    create_token_with_package_grant,
    create_token_with_package_map,
    create_taj_token,
    decode_token,
    validate_token,
    validate_package_token,
    validate_package_map_token,
    validate_taj_token,

    # Models
    Scope, AuthRequest, Decision,
    PackageAccessRequest, PackageMapToken,
    PackageToken, S3Location, TajToken, Token,
    PackageMap,

    # Scope utilities
    parse_scope, format_scope, is_subset,

    # Exceptions
    RajaError, AuthorizationError, InsufficientScopesError,
    ScopeError, ScopeParseError, ScopeValidationError,
    TokenError, TokenExpiredError, TokenInvalidError, TokenValidationError,
)
```

## Key Concepts

### Scopes

Format: `ResourceType:ResourceId:Action` — used internally for subset checking.

Wildcard `*` matches any value in that segment. `*:*:*` is full admin access.

### TAJ Tokens (Trusted Authorization JWT)

TAJ tokens carry S3 path-based grants (e.g. `s3:GetObject/bucket/prefix/`) rather than Cedar scope strings. Issued by the RALE authorizer after DataZone membership is verified.

### Enforcement

Pure subset checking — no policy evaluation at enforcement time. The enforcer decodes the token, extracts grants, and checks if the requested resource/action is covered.

## Design Principles

- **Fail-closed:** Unknown or ambiguous requests default to DENY
- **Pure functions:** Same inputs always produce same outputs
- **Type safety:** Full Pydantic models, mypy strict mode

## Testing

```bash
./poe test-unit       # Unit tests (no AWS)
./poe test-integration  # Requires deployed AWS resources
./poe test-cov        # With coverage
```
