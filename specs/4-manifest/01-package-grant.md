# Package Grant Design: Package-Based Authorization

## Executive Summary

This document specifies the design for **package grants** - a content-based authorization model where authority is anchored to **immutable Quilt package packages** rather than mutable S3 paths.

**Core Hypothesis:**

> Authority should be defined by **what data means** (packages), not **where data lives** (paths).

**Key Innovation:**

Cedar policies reference immutable package identifiers. RAJEE enforces by resolving packages and checking membership. No policy explosion, no file enumeration, fail-closed semantics preserved.

---

## 1. Problem Statement

### 1.1 Limitations of Path-Based Authorization

Path grants (prefix matching) work well for:

- Infrastructure data
- Shared buckets
- Operational workflows

But they break down for:

- **Packages with thousands of files** → Policy explosion
- **Cross-bucket layouts** → Complex prefix logic
- **Mutable structure** → Silent scope expansion
- **Semantic meaning** → Paths don't capture what data represents

### 1.2 The Package Grant Solution

Instead of:

```
"grant read access to s3://bucket/dataset/*"
```

We want:

```
"grant read access to quilt+s3://registry#package=my/pkg@abc123def456"
```

The package:

- Is **immutable** (content-addressed)
- Defines **exact membership** (which files belong)
- Carries **semantic meaning** (what the data represents)
- Scales to **arbitrary file counts** (one grant, many files)

---

## 2. Design Principles

### 2.1 Packages Constrain Authority, Don't Grant It

The package is **evidence**, not **authority**:

- Authority originates in Cedar policies
- Cedar grants access to a package identifier
- RAJA mints a capability (RAJ) referencing the package
- RAJEE enforces by checking membership against the package
- The package **bounds** what can be accessed, but does not create permission

### 2.2 Immutability is Non-Negotiable

Packages used for authorization MUST be immutable:

- Content-addressed by hash
- Meaning never changes
- Safe to cache indefinitely
- No silent scope expansion

### 2.3 Fail-Closed Semantics

All failure modes deny access:

- Package not found → DENY
- Package parse error → DENY
- File not in package → DENY
- Invalid RAJ → DENY

### 2.4 Zero File Enumeration in Policy

Cedar policies NEVER enumerate files:

```cedar
// ✅ CORRECT: Reference the immutable package
permit(
  principal == Role::"analyst",
  action == Action::"quilt:ReadPackage",
  resource == Package::"quilt+s3://bucket#package=my/pkg@abc123def456"
);

// ❌ WRONG: Never enumerate files in policy
permit(
  principal == Role::"analyst",
  action == Action::"s3:GetObject",
  resource in [
    S3Object::"bucket/file1.txt",
    S3Object::"bucket/file2.txt",
    // ... 10,000 more files
  ]
);
```

---

## 3. Quilt+ URI Scheme

### 3.1 Format

Quilt+ URIs uniquely identify immutable package versions:

```
quilt+{storage}://{registry}#package={package_name}@{hash}[&path={object}]
```

**Components:**

- `storage`: Storage backend (`s3`, `file`, etc.)
- `registry`: Registry location (bucket or path)
- `package`: Package name (required)
- `hash`: Content hash identifying immutable version (required)
- `path`: Optional path to specific object within package

**Requirements:**

- Hash MUST be present (no mutable references allowed)
- Path is optional (omit to reference entire package)

### 3.2 Examples

```
# Package pinned to specific hash
quilt+s3://quilt-prod-registry#package=my/pkg@abc123def456

# Package with specific object path
quilt+s3://quilt-dev-registry#package=my/pkg@abc123def456&path=data/file.csv

# Local testing with hash
quilt+file:///local/registry#package=test/data@deadbeef1234
```

### 3.3 Immutability Guarantees

- **Content hashes** (`@abc123...`) → Intrinsically immutable
- **Hash is required** → No mutable references allowed
- **Path is optional** → Can reference entire package or specific object

### 3.4 URI Normalization

URIs MUST be canonicalized before use:

1. Lowercase scheme and storage type
2. Remove trailing slashes
3. Validate hash is present (no mutable refs)
4. Normalize path separators if path is specified

```python
# ✅ Valid for authorization (hash-pinned package)
"quilt+s3://bucket#package=my/pkg@a1b2c3d4"

# ✅ Valid with path
"quilt+s3://bucket#package=my/pkg@a1b2c3d4&path=data/file.csv"

# ❌ Invalid - missing hash
"quilt+s3://bucket#package=my/pkg"

# ❌ Invalid format - wrong separator
"quilt+s3://bucket?package=my/pkg@a1b2c3d4"
```

---

## 4. Cedar Model

### 4.1 Entity Model

**New entity type: `Package`**

```cedar
entity Package {
  // Quilt+ URI (immutable package identifier)
  // Format: quilt+{storage}://{registry}#package={name}@{hash}[&path={object}]
  uri: String,

  // Package metadata (optional, for policy conditions)
  packageName: String,
  hash: String,
};
```

**Example instance:**

```cedar
// Entity ID is the Quilt+ URI
Package::"quilt+s3://prod-registry#package=my/pkg@abc123def456"
```

### 4.2 Action Model

**New package-level actions:**

```cedar
action "quilt:ReadPackage" appliesTo {
  principal: [Role, User],
  resource: [Package]
};

action "quilt:WritePackage" appliesTo {
  principal: [Role, User],
  resource: [Package]
};
```

**Key distinction:**

- `quilt:ReadPackage` → Grant read access to package contents
- `s3:GetObject` → Low-level S3 action (still used internally by RAJEE)

### 4.3 Example Policies

**Grant read access to specific package version:**

```cedar
permit(
  principal == Role::"analyst",
  action == Action::"quilt:ReadPackage",
  resource == Package::"quilt+s3://prod#package=sales-data@abc123def456"
);
```

**Grant read access to all packages with specific name:**

```cedar
permit(
  principal == Role::"data-scientist",
  action == Action::"quilt:ReadPackage",
  resource
)
when {
  resource.packageName == "ml-training-data"
};
```

**Grant write access to specific package (for pipelines):**

```cedar
permit(
  principal == Role::"etl-pipeline",
  action == Action::"quilt:WritePackage",
  resource == Package::"quilt+s3://staging#package=raw-data@deadbeef1234"
);
```

---

## 5. Control Plane: Token Issuance

### 5.1 Authorization Request

Client requests access to a package:

```http
POST /token HTTP/1.1
Content-Type: application/json

{
  "principal": "Role::analyst",
  "resource": "Package::\"quilt+s3://prod#package=my/pkg@abc123def456\"",
  "action": "quilt:ReadPackage",
  "context": {
    "time": "2024-01-15T10:00:00Z"
  }
}
```

### 5.2 RAJA Decision Flow

```
1. Validate request
   ├─ Principal exists?
   ├─ Resource is a valid Quilt+ URI?
   ├─ Quilt+ URI has required hash? (immutable)
   └─ Action is valid?

2. Query Cedar (AVP)
   ├─ principal: Role::analyst
   ├─ action: quilt:ReadPackage
   ├─ resource: Package::"quilt+s3://prod#package=my/pkg@abc123def456"
   └─ context: {...}

3. Cedar returns ALLOW or DENY

4. If ALLOW, mint RAJ with Quilt+ URI in quilt_uri claim
```

### 5.3 RAJ (JWT) Structure

The RAJ contains only **mechanically enforceable** claims:

```json
{
  "sub": "Role::analyst",
  "aud": "rajee.quiltdata.com",
  "iss": "raja.quiltdata.com",
  "iat": 1705315200,
  "exp": 1705315500,
  "nbf": 1705315200,

  "quilt_uri": "quilt+s3://prod#package=my/pkg@abc123def456",
  "mode": "read",

  "audit": {
    "request_id": "req-abc123",
    "policy_store": "ps-xyz789"
  }
}
```

**Key claims:**

- `quilt_uri`: Quilt+ URI identifying the immutable package
- `mode`: `read` or `readwrite`
- No file lists, no buckets, no paths

**What's NOT in the RAJ:**

- ❌ List of files
- ❌ S3 buckets
- ❌ Path prefixes
- ❌ Mutable references

The RAJ is a **capability**: possession implies bounded authority. The Quilt+ URI serves as the authoritative package identifier.

---

## 6. Data Plane: RAJEE Enforcement

### 6.1 Architecture

```
┌─────────────────────────────────────────────────┐
│                  Client Request                  │
│  GET /s3/my-bucket/path/to/file.csv             │
│  Authorization: Bearer <RAJ>                     │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│              Envoy Proxy (RAJEE)                │
│                                                  │
│  1. Validate RAJ (signature, expiry, audience)  │
│  2. Extract quilt_uri (Quilt+ URI) from RAJ  │
│  3. Resolve Quilt+ URI → file membership list   │
│  4. Check: (bucket, key) ∈ package?            │
│  5. If yes: proxy to S3; if no: 403             │
└────────────────────┬────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────┐
│            Amazon S3 (Protected)                │
│  RAJEE has IAM role to access buckets           │
└─────────────────────────────────────────────────┘
```

### 6.2 Package Resolution

**Challenge:** RAJEE needs to resolve Quilt+ URI → list of `(bucket, key)` tuples.

**Option A: Lambda Authorizer (Recommended)**

Envoy calls an AWS Lambda external authorizer:

```
┌──────────┐  HTTP POST   ┌─────────────────┐
│  Envoy   │─────────────>│ Lambda Authorizer│
│          │              │                  │
│          │  1. RAJ      │ - Validate JWT   │
│          │  2. Request  │ - Resolve package│
│          │              │ - Check membership│
│          │<─────────────│                  │
│          │  Allow/Deny  └─────────────────┘
└──────────┘                      │
                                  │ Uses quilt3
                                  ▼
                          ┌─────────────┐
                          │ S3 Package │
                          │   Storage   │
                          └─────────────┘
```

**Lambda logic:**

```python
import quilt3
from typing import Tuple, List

def resolve_package(quilt_uri: str) -> List[Tuple[str, str]]:
    """
    Resolve Quilt+ URI to list of (bucket, key) tuples.

    Args:
        quilt_uri: Quilt+ URI (e.g., "quilt+s3://bucket#package=name@abc123def456")

    Returns:
        List of (bucket, key) for all physical keys in package

    Raises:
        PackageNotFound: If package doesn't exist
        PackageInvalid: If package is corrupt or URI is malformed
    """
    # Parse Quilt+ URI
    uri = parse_quilt_uri(quilt_uri)

    # Fetch package using quilt3
    pkg = quilt3.Package.browse(
        name=uri.package,
        registry=f"s3://{uri.registry}",
        top_hash=uri.version
    )

    # Extract physical keys
    physical_keys = []
    for logical_path, entry in pkg.walk():
        physical_keys.append((entry.bucket, entry.key))

    return physical_keys

def authorize(raj: JWT, bucket: str, key: str) -> bool:
    """
    Check if (bucket, key) is authorized by RAJ.
    """
    # 1. Validate RAJ
    if not validate_jwt(raj):
        return False

    # 2. Extract quilt_uri (Quilt+ URI)
    quilt_uri = raj.claims["quilt_uri"]

    # 3. Resolve package (with caching)
    physical_keys = resolve_package_cached(quilt_uri)

    # 4. Check membership
    return (bucket, key) in physical_keys
```

**Option B: Pre-compiled Package Cache**

During token issuance, compile package to DynamoDB:

```
Token issuance time:
  1. Cedar allows access to package
  2. Resolve Quilt+ URI → list of (bucket, key)
  3. Store in DynamoDB: quilt_uri → [physical_keys]
  4. Mint RAJ with quilt_uri claim

Enforcement time:
  1. Validate RAJ
  2. Query DynamoDB: quilt_uri → [physical_keys]
  3. Check membership
```

**Trade-offs:**

| Approach | Pros | Cons |
|----------|------|------|
| Lambda Authorizer | - Standard Envoy pattern<br>- No pre-compilation<br>- Works with any package | - Lambda cold start<br>- quilt3 dependency<br>- Network latency |
| Pre-compiled Cache | - Fast lookup (DynamoDB)<br>- No cold start<br>- Pure membership check | - Requires pre-compilation<br>- DynamoDB storage cost<br>- Cache invalidation |

**Recommendation:** Start with **Lambda Authorizer** for flexibility, optimize to cache later if needed.

### 6.3 Enforcement Algorithm

```python
def enforce_package_grant(
    raj: JWT,
    request: S3Request
) -> Decision:
    """
    Enforce package-based authorization.

    Fail-closed: Any error returns DENY.
    """
    try:
        # 1. Validate RAJ
        if not validate_jwt(raj, expected_audience="rajee"):
            return Decision.DENY("Invalid JWT")

        if jwt_expired(raj):
            return Decision.DENY("Token expired")

        # 2. Extract claims
        quilt_uri = raj.claims.get("quilt_uri")
        mode = raj.claims.get("mode")  # "read" or "readwrite"

        if not quilt_uri or not mode:
            return Decision.DENY("Missing required claims")

        # 3. Check action compatibility
        if request.action == "GetObject" and mode not in ["read", "readwrite"]:
            return Decision.DENY("Action not permitted by token mode")

        if request.action == "PutObject" and mode != "readwrite":
            return Decision.DENY("Write action requires readwrite mode")

        # 4. Resolve package from Quilt+ URI
        physical_keys = resolve_package(quilt_uri)

        # 5. Check membership
        requested = (request.bucket, request.key)
        if requested in physical_keys:
            return Decision.ALLOW(
                reason=f"Object is member of package {quilt_uri}",
                quilt_uri=quilt_uri,
                matched_key=f"s3://{request.bucket}/{request.key}"
            )
        else:
            return Decision.DENY(
                reason=f"Object not in package {quilt_uri}",
                quilt_uri=quilt_uri,
                requested_key=f"s3://{request.bucket}/{request.key}"
            )

    except PackageNotFound:
        return Decision.DENY("Package not found")
    except Exception as e:
        # Fail closed on any error
        log_error(e)
        return Decision.DENY("Internal error")
```

### 6.4 Caching Strategy

To avoid repeated package resolution:

**Cache key:** `quilt_uri` (Quilt+ URI)

**Cache value:** `List[(bucket, key)]`

**Cache location:**

- In-memory (Lambda) with TTL
- ElastiCache (Redis) for shared cache
- DynamoDB with GSI for distributed cache

**Cache TTL:**

- Immutable packages: **Infinite** (or very long, e.g., 30 days)
- Cache key: `f"package:{hash(quilt_uri)}"`

**Cache invalidation:**

- Not needed (Quilt+ URIs are immutable by design)
- If URI is mutable (shouldn't be allowed), TTL = 0

---

## 7. Integration with Existing Path Grants

### 7.1 Two Grant Types Coexist

RAJA supports **both** grant types:

1. **Path grants** → Prefix-based authorization (existing)
2. **Package grants** → Content-based authorization (new)

### 7.2 Token Structure

A RAJ may contain **either**:

```json
// Path grant
{
  "grants": ["s3:GetObject/bucket/prefix/"]
}

// Package grant
{
  "quilt_uri": "quilt+s3://registry#package=my/pkg@abc123def456",
  "mode": "read"
}

// NOT BOTH in same token (keep tokens focused)
```

### 7.3 Enforcement Routing

RAJEE checks token type and routes accordingly:

```python
def enforce(raj: JWT, request: S3Request) -> Decision:
    if "grants" in raj.claims:
        return enforce_prefix_grant(raj, request)
    elif "quilt_uri" in raj.claims:
        return enforce_package_grant(raj, request)
    else:
        return Decision.DENY("Unknown token type")
```

---

## 8. Security Considerations

### 8.1 Package Integrity

**Threat:** Attacker modifies package to expand authorized set

**Mitigation:**

- Packages stored in trusted, immutable storage (S3 with versioning)
- quilt3 validates package signatures/hashes
- RAJEE only trusts packages from authorized registries

### 8.2 Package Resolution DoS

**Threat:** Attacker requests access to package with millions of files

**Mitigation:**

- Rate limit package resolution API
- Cache resolved packages
- Set maximum package size in policy

### 8.3 Token Scope Creep

**Threat:** Long-lived tokens reference packages that "should" have changed

**Mitigation:**

- Short token TTL (5 minutes)
- Packages are immutable (no silent expansion)
- Token revocation via deny-list (if needed)

### 8.4 Registry Compromise

**Threat:** Attacker modifies registry to serve malicious packages

**Mitigation:**

- Registry backed by S3 with bucket policies
- Package signatures (quilt3 native feature)
- Audit logging for package access

---

## 9. Implementation Plan

### Phase 1: Cedar Schema Extension

**Tasks:**

1. Define `Package` entity type
2. Define `quilt:ReadPackage` and `quilt:WritePackage` actions
3. Update Cedar schema in AVP
4. Write example policies

**Files:**

- `policies/schema.cedar` - Add Package entity
- `policies/package-grants/` - Example policies

### Phase 2: Control Plane (RAJA)

**Tasks:**

1. Add Quilt+ URI parser and validator
2. Update token issuance to handle package grants
3. Add quilt_uri claim to RAJ structure
4. Update token introspection endpoint

**Files:**

- `src/raja/models.py` - Add PackageGrant model
- `src/raja/token.py` - Support quilt_uri claim
- `src/raja/quilt_uri.py` - New module for Quilt+ URI parsing

### Phase 3: Data Plane (RAJEE) - Lambda Authorizer

**Tasks:**

1. Create Lambda authorizer function
2. Implement package resolution using quilt3
3. Add membership checking logic
4. Add caching layer
5. Wire to Envoy as external authorizer

**Files:**

- `lambda_handlers/package_authorizer/handler.py` - New authorizer
- `lambda_handlers/package_authorizer/resolver.py` - Package resolution
- `infra/raja_poc/constructs/package_authorizer.py` - CDK construct

### Phase 4: Testing

**Tasks:**

1. Unit tests for Quilt+ URI parsing
2. Unit tests for package resolution (mock quilt3)
3. Integration tests with real packages
4. Property-based tests (package immutability)
5. Security tests (invalid URIs, missing packages)

**Files:**

- `tests/unit/test_quilt_uri.py`
- `tests/unit/test_package_resolution.py`
- `tests/integration/test_package_grants.py`

### Phase 5: Documentation

**Tasks:**

1. Update design docs
2. Write user guide for package grants
3. Add examples to README
4. Create admin guide

**Files:**

- `docs/package-grants.md`
- `docs/admin-guide.md`
- `README.md` - Update with package examples

---

## 10. Success Criteria

### Functional

- [ ] Cedar policies can reference Package resources
- [ ] RAJA mints RAJs with quilt_uri claim (Quilt+ URI)
- [ ] RAJEE resolves Quilt+ URIs using quilt3
- [ ] RAJEE enforces membership correctly
- [ ] Path grants and package grants coexist
- [ ] Invalid/mutable URIs rejected at token issuance

### Performance

- [ ] Package resolution < 100ms p99 (with cold cache)
- [ ] Package resolution < 10ms p99 (with warm cache)
- [ ] Authorization decision < 50ms p99 (total)
- [ ] Cache hit rate > 95% for repeated package access

### Security

- [ ] Packages verified immutable at token issuance
- [ ] Package resolution fails closed on errors
- [ ] Token expiration enforced
- [ ] No path traversal vulnerabilities
- [ ] Audit logging for all package resolutions

### Scale

- [ ] Support packages with 10,000+ files
- [ ] Support 100+ concurrent package resolutions
- [ ] Cache scales horizontally (Redis/DynamoDB)

---

## 11. Open Questions

### 11.1 Package Resolution Service

**Question:** Should package resolution be:

- A: Lambda authorizer (one function, simple)
- B: Dedicated service (more control, better caching)

**Recommendation:** Start with A (Lambda), move to B if needed.

### 11.2 Cross-Bucket Packages

**Question:** How to handle packages spanning multiple buckets?

**Answer:** RAJEE needs IAM permissions to all buckets referenced in package. Configure via CDK.

### 11.3 Write Operations

**Question:** Should `quilt:WritePackage` allow modifying existing packages?

**Answer:** No. Write grants are for **creating new package versions**, not modifying existing (immutable) ones.

---

## 12. Alternatives Considered

### 12.1 Enumerate Files in Cedar Policy

**Rejected:** Does not scale. A package with 10,000 files would create a 10,000-line policy.

### 12.2 Store Package in Token

**Rejected:** JWTs have size limits. Cannot fit large packages.

### 12.3 Use S3 Select on Package

**Rejected:** Adds complexity, still requires package fetch. Lambda + quilt3 is simpler.

### 12.4 Hybrid: Prefix + Package

**Rejected:** Confusing semantics. Keep grant types distinct.

---

## 13. References

- **Quilt3 Documentation:** <https://docs.quiltdata.com/>
- **Cedar Documentation:** <https://www.cedarpolicy.com/>
- **Envoy External Authorization:** <https://www.envoyproxy.io/docs/envoy/latest/configuration/http/http_filters/ext_authz_filter>
- **GitHub Issue #29:** Package authority feature request
- **Related Spec:** [rajee-package.md](../../docs/rajee-package.md)

---

## 14. Summary

Package grants solve the package authorization problem by:

1. **Anchoring authority to immutable identifiers** (Quilt+ URIs)
2. **Keeping policies simple** (one policy per package, not per file)
3. **Preserving fail-closed semantics** (unknown requests denied)
4. **Scaling to arbitrary file counts** (package resolution is cached)
5. **Maintaining semantic clarity** (authorize what data means, not where it lives)

This design extends RAJA/RAJEE to support **content-based authorization** while maintaining the existing **location-based** (prefix) model for operational workflows.

**Next Steps:**

1. Review this design
2. Prototype Quilt+ URI parser
3. Implement Lambda authorizer with quilt3
4. Test with real Quilt packages
5. Deploy and measure performance
