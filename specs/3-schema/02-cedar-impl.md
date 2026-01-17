# Implementation Spec: Hierarchical S3 Resources with Prefix Matching

## Overview

This spec defines the implementation tasks to support hierarchical S3 resource syntax with prefix matching across the entire RAJA stack:

1. Cedar policy files (source of truth)
2. Policy compiler (Cedar → Scopes)
3. Token service (Scopes → JWT claims)
4. Lua enforcer (JWT + request → ALLOW/DENY)

## Key Design Decisions

### Remove Non-Definitive Authorization Paths

**CRITICAL:** Remove the Python `is_authorized` endpoint entirely. It was a temporary POC and is not the definitive enforcement point.

**Rationale:**

- Lua enforcer in Envoy is the single source of truth for authorization decisions
- Maintaining multiple enforcement implementations creates consistency issues
- Python authorizer cannot see actual S3 requests (missing headers, multipart context, etc.)

**Action Items:**

- Remove `/authorize` endpoint from control plane Lambda
- Remove `is_authorized` tests that don't go through Envoy
- Update documentation to reflect Lua enforcer as the only enforcement point
- Keep Python enforcer logic ONLY for unit testing scope matching algorithms

### Single Enforcement Point: Lua in Envoy

All authorization decisions MUST flow through:

```
S3 Request → Envoy (Lua) → External Auth (validate JWT) → Lua Enforcer → ALLOW/DENY
```

## Cedar Policy Syntax

### Current (Embedded Bucket+Key)

```cedar
resource == Raja::S3Object::"bucket-name/key/path"
```

**Problems:**

- Cannot distinguish bucket from key
- Cannot do independent prefix matching
- Requires hardcoding account/region

### Target (Hierarchical)

```cedar
resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
```

**Benefits:**

- Clear bucket vs. key separation
- Independent prefix matching on each component
- No hardcoded account/region (use prefix matching)

### Bucket-Only Policies

```cedar
resource == Raja::S3Bucket::"raja-poc-test-"
```

For actions that operate on buckets (ListBucket, GetBucketLocation, etc.).

## Scope Format

### Current Format

```
S3Object:bucket/key:action
```

### New Format (Preserves Hierarchy)

```
ResourceType:bucket-component/key-component:action
```

**Examples:**

```
S3Object:raja-poc-test-/rajee-integration/:s3:GetObject
S3Object:raja-poc-test-/rajee-integration/:s3:PutObject
S3Bucket:raja-poc-test-:s3:ListBucket
```

**Key properties:**

- Format: `ResourceType:bucket-or-prefix/key-or-prefix:action`
- Trailing `/` or `-` indicates prefix match
- No trailing indicator means exact match
- For bucket-only scopes: `S3Bucket:bucket-or-prefix:action` (no `/` separator)

## Implementation Tasks

### 1. Cedar Policy Parser Updates

**File:** `src/raja/cedar/parser.py`

**Tasks:**

- [ ] Parse hierarchical `in` syntax: `resource == Type::"id" in Parent::"parent-id"`
- [ ] Extract both resource and parent components separately
- [ ] Preserve original syntax for scope compilation
- [ ] Handle bucket-only policies (no `in` clause)
- [ ] Validate syntax matches schema (S3Object in S3Bucket hierarchy)

**Parsing Output:**

```python
{
    "resource_type": "S3Object",
    "resource_id": "rajee-integration/",
    "parent_type": "S3Bucket",
    "parent_id": "raja-poc-test-",
    "action": "s3:GetObject"
}
```

**Edge Cases:**

- Bucket-only policies: `parent_type` and `parent_id` are None
- Exact matches: No trailing `/` or `-`
- Multiple `in` clauses (not supported in MVP - should error)

### 2. Scope Compiler Updates

**File:** `lambda_handlers/compiler/handler.py`

**Tasks:**

- [ ] Generate scopes from hierarchical Cedar syntax
- [ ] Combine bucket and key components: `bucket-prefix/key-prefix`
- [ ] Detect prefix indicators (trailing `/` or `-`)
- [ ] Store scope format: `ResourceType:bucket/key:action`
- [ ] Handle bucket-only scopes: `ResourceType:bucket:action`

**Compilation Logic:**

```
Cedar: resource == Raja::S3Object::"key" in Raja::S3Bucket::"bucket"
↓
Scope: S3Object:bucket/key:action
```

**Scope Storage:**

- DynamoDB principal table: Store compiled scopes per principal
- Include metadata: policy ID, last compiled timestamp
- Scope deduplication (multiple policies → same scope)

### 3. Token Service Updates

**File:** `lambda_handlers/token_service/handler.py`

**Tasks:**

- [ ] Read compiled scopes from DynamoDB
- [ ] Include scopes in JWT claims (no transformation needed)
- [ ] JWT format unchanged: `{"scopes": ["S3Object:bucket/key:action", ...]}`

**No changes required** - token service is scope-agnostic.

### 4. Lua Enforcer Updates (CRITICAL)

**File:** `infra/raja_poc/envoy_config/lua/external_auth.lua`

**Tasks:**

- [ ] Parse S3 request to extract bucket and key
- [ ] Handle all S3 operation types (see test cases below)
- [ ] Extract scopes from validated JWT
- [ ] Implement prefix matching algorithm
- [ ] Log authorization decisions with details

**Prefix Matching Algorithm:**

```lua
function matches_prefix(granted_scope, requested_bucket, requested_key, requested_action)
    -- Parse granted scope: "S3Object:bucket-prefix/key-prefix:action"
    local granted_bucket, granted_key, granted_action = parse_scope(granted_scope)

    -- Check action match (exact)
    if granted_action ~= requested_action then
        return false
    end

    -- Check bucket match
    if not matches_component(granted_bucket, requested_bucket) then
        return false
    end

    -- Check key match (if applicable)
    if granted_key then
        if not matches_component(granted_key, requested_key) then
            return false
        end
    end

    return true
end

function matches_component(granted, requested)
    -- If granted ends with '/' or '-', it's a prefix match
    if ends_with(granted, '/') or ends_with(granted, '-') then
        return starts_with(requested, granted)
    else
        -- Exact match
        return granted == requested
    end
end
```

**S3 Request Parsing:**

Must handle various S3 API patterns:

```lua
-- GetObject: GET /bucket/key/path
-- PutObject: PUT /bucket/key/path
-- DeleteObject: DELETE /bucket/key/path
-- ListBucket: GET /bucket?list-type=2&prefix=key/path
-- InitiateMultipartUpload: POST /bucket/key?uploads
-- UploadPart: PUT /bucket/key?partNumber=1&uploadId=xyz
-- CompleteMultipartUpload: POST /bucket/key?uploadId=xyz
-- AbortMultipartUpload: DELETE /bucket/key?uploadId=xyz
-- HeadObject: HEAD /bucket/key/path
-- GetBucketLocation: GET /bucket?location

-- Versioned operations:
-- GetObjectVersion: GET /bucket/key?versionId=xyz
-- DeleteObjectVersion: DELETE /bucket/key?versionId=xyz
-- ListBucketVersions: GET /bucket?versions
-- GetObjectVersionTagging: GET /bucket/key?versionId=xyz&tagging
-- PutObjectVersionTagging: PUT /bucket/key?versionId=xyz&tagging
```

**Action Mapping:**

Map HTTP method + path + query to S3 action:

```lua
function get_s3_action(method, path, query_params)
    -- Versioned operations
    if query_params["versionId"] then
        if query_params["tagging"] and method == "GET" then
            return "s3:GetObjectVersionTagging"
        elseif query_params["tagging"] and method == "PUT" then
            return "s3:PutObjectVersionTagging"
        elseif method == "GET" then
            return "s3:GetObjectVersion"
        elseif method == "DELETE" then
            return "s3:DeleteObjectVersion"
        end
    end

    -- Multipart operations
    if query_params["uploads"] then
        return "s3:InitiateMultipartUpload"
    elseif query_params["uploadId"] and query_params["partNumber"] then
        return "s3:UploadPart"
    elseif query_params["uploadId"] and method == "POST" then
        return "s3:CompleteMultipartUpload"
    elseif query_params["uploadId"] and method == "DELETE" then
        return "s3:AbortMultipartUpload"
    end

    -- Bucket operations
    if query_params["versions"] then
        return "s3:ListBucketVersions"
    elseif query_params["list-type"] or query_params["prefix"] then
        return "s3:ListBucket"
    elseif query_params["location"] then
        return "s3:GetBucketLocation"
    end

    -- Object operations
    if method == "GET" then
        return "s3:GetObject"
    elseif method == "PUT" then
        return "s3:PutObject"
    elseif method == "DELETE" then
        return "s3:DeleteObject"
    elseif method == "HEAD" then
        return "s3:HeadObject"
    else
        return nil  -- Unknown action → DENY
    end
end
```

### 5. Python Enforcer (Unit Test Only)

**File:** `src/raja/enforcer.py`

**Purpose:** Unit test the prefix matching algorithm in isolation (no AWS dependencies).

**Tasks:**

- [ ] Implement same prefix matching logic as Lua
- [ ] Used ONLY in unit tests (`tests/unit/test_enforcer.py`)
- [ ] NOT exposed via API endpoint
- [ ] Serves as reference implementation for Lua

**Function signature:**

```python
def is_prefix_match(granted_scope: str, requested_scope: str) -> bool:
    """Check if requested scope matches granted scope (with prefix matching)."""
    pass
```

### 6. Cedar Schema Updates

**File:** `policies/schema.cedar`

**Tasks:**

- [ ] Verify schema already supports hierarchy: `entity S3Object in [S3Bucket] {}`
- [ ] Add actions for multipart upload operations
- [ ] Document expected resource syntax in comments

**New Actions to Add:**

```cedar
action "s3:InitiateMultipartUpload" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:UploadPart" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:CompleteMultipartUpload" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:AbortMultipartUpload" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:HeadObject" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:GetObjectVersion" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:PutObjectVersionTagging" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:GetObjectVersionTagging" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:DeleteObjectVersion" appliesTo {
  principal: [User, Role],
  resource: [S3Object]
}

action "s3:ListBucketVersions" appliesTo {
  principal: [User, Role],
  resource: [S3Bucket]
}
```

### 7. Policy File Updates

**Files:**

- `policies/rajee_test_policy.cedar`
- `policies/rajee_integration_test.cedar`

**Tasks:**

- [ ] Rewrite to use hierarchical syntax
- [ ] Use prefix indicators: `raja-poc-test-` for bucket, `rajee-integration/` for key
- [ ] Add structured annotations for traceability
- [ ] Remove any remaining `*` wildcards

**Example:**

```cedar
// @description Grant test-user access to rajee-integration/ prefix in test buckets
// @test tests/integration/test_rajee_envoy_bucket.py::test_get_object_with_valid_token
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);
```

### 8. Remove Non-Definitive Authorization

**Files to Update:**

- [ ] `lambda_handlers/control_plane/handler.py` - Remove `/authorize` endpoint
- [ ] `tests/integration/test_authorizer.py` - Remove or update tests
- [ ] `README.md` / `CLAUDE.md` - Remove references to Python authorizer endpoint

**Rationale:**

- Envoy Lua enforcer is the single source of truth
- Python authorizer lacks full request context (headers, multipart state)
- Maintaining two enforcement implementations is error-prone

**Keep:**

- Python `enforcer.py` module for unit testing prefix matching logic
- Unit tests in `tests/unit/test_enforcer.py`

## Test Coverage

### Unit Tests

**File:** `tests/unit/test_enforcer.py`

Test prefix matching algorithm in isolation:

- [ ] Exact match: `bucket/key` matches `bucket/key`
- [ ] Prefix match bucket: `bucket-` matches `bucket-123`, `bucket-abc`
- [ ] Prefix match key: `prefix/` matches `prefix/file.txt`, `prefix/subdir/file.txt`
- [ ] No match: `bucket-` does not match `other-bucket`
- [ ] No match: `prefix/` does not match `other-prefix/file.txt`
- [ ] Action mismatch: Same resource, different action → DENY
- [ ] Bucket-only scope: `S3Bucket:bucket-:s3:ListBucket` matches bucket operations

**File:** `tests/unit/test_cedar_parser.py`

Test hierarchical syntax parsing:

- [ ] Parse `resource == Type::"id" in Parent::"parent-id"`
- [ ] Parse bucket-only policies (no `in` clause)
- [ ] Error on invalid syntax
- [ ] Extract all components correctly

### Integration Tests

**File:** `tests/integration/test_rajee_envoy_bucket.py`

Test actual S3 operations through Envoy:

#### Basic Operations

- [ ] **GET object with valid token** - `s3:GetObject` on `rajee-integration/test.txt`
- [ ] **PUT object with valid token** - `s3:PutObject` on `rajee-integration/test.txt`
- [ ] **DELETE object with valid token** - `s3:DeleteObject` on `rajee-integration/test.txt`
- [ ] **LIST bucket with valid token** - `s3:ListBucket` on bucket with `rajee-integration/` prefix filter

#### Prefix Matching

- [ ] **GET object in subdirectory** - `s3:GetObject` on `rajee-integration/subdir/file.txt`
- [ ] **GET object outside prefix** - `s3:GetObject` on `other-prefix/file.txt` → DENY
- [ ] **PUT to different bucket** - `s3:PutObject` to bucket not matching `raja-poc-test-` → DENY

#### Multipart Upload

- [ ] **Initiate multipart upload** - `POST /bucket/key?uploads`
  - Policy should grant `s3:InitiateMultipartUpload`
  - Or grant `s3:PutObject` (some systems use this)

- [ ] **Upload parts** - `PUT /bucket/key?partNumber=N&uploadId=xyz`
  - Policy should grant `s3:UploadPart`
  - Test multiple parts (part 1, 2, 3)

- [ ] **Complete multipart upload** - `POST /bucket/key?uploadId=xyz`
  - Policy should grant `s3:CompleteMultipartUpload`
  - Verify object exists after completion

- [ ] **Abort multipart upload** - `DELETE /bucket/key?uploadId=xyz`
  - Policy should grant `s3:AbortMultipartUpload`
  - Verify upload is canceled

#### Versioned Operations

**Reference:** [Quilt Cross-Account Setup - Why versioning is required](https://docs.quilt.bio/quilt-platform-administrator/crossaccount#why-cross-account-setup)

- [ ] **Get object version** - `GET /bucket/key?versionId=xyz`
  - Policy should grant `s3:GetObjectVersion`
  - Verify specific version is returned

- [ ] **Delete object version** - `DELETE /bucket/key?versionId=xyz`
  - Policy should grant `s3:DeleteObjectVersion`
  - Verify only that version is deleted (not latest)

- [ ] **List bucket versions** - `GET /bucket?versions`
  - Policy should grant `s3:ListBucketVersions`
  - Verify version history is returned

- [ ] **Get object version tagging** - `GET /bucket/key?versionId=xyz&tagging`
  - Policy should grant `s3:GetObjectVersionTagging`

- [ ] **Put object version tagging** - `PUT /bucket/key?versionId=xyz&tagging`
  - Policy should grant `s3:PutObjectVersionTagging`

#### Edge Cases

- [ ] **HEAD object** - `s3:HeadObject` should work with `GetObject` or require separate permission
- [ ] **LIST with prefix filter** - Verify `?prefix=rajee-integration/subdir/` works
- [ ] **Empty key** - `GET /bucket/` (no key) → Should this be allowed or denied?
- [ ] **Trailing slash in key** - `rajee-integration/` vs `rajee-integration` handling
- [ ] **Invalid tokens** - Expired, wrong signature, missing scopes → All DENY
- [ ] **Missing action in scopes** - Token has `GetObject` but request is `PutObject` → DENY
- [ ] **Version without permission** - Request with `?versionId=xyz` but only has `s3:GetObject` (not `s3:GetObjectVersion`) → DENY

### Policy Questions to Resolve

**Do we need separate permissions for multipart operations?**

Option A: Separate permissions (more granular)

```cedar
permit(action == Raja::Action::"s3:PutObject", ...)
permit(action == Raja::Action::"s3:InitiateMultipartUpload", ...)
permit(action == Raja::Action::"s3:UploadPart", ...)
permit(action == Raja::Action::"s3:CompleteMultipartUpload", ...)
```

Option B: PutObject implies multipart (simpler)

```cedar
permit(action == Raja::Action::"s3:PutObject", ...)
// Also allows all multipart operations on same resource
```

**Recommendation:** Start with Option B for MVP (fewer policies), add Option A if users need granular control.

**Do we need HeadObject permission?**

Option A: HeadObject is separate

```cedar
permit(action == Raja::Action::"s3:HeadObject", ...)
```

Option B: GetObject implies HeadObject (AWS S3 pattern)

```cedar
permit(action == Raja::Action::"s3:GetObject", ...)
// Also allows HEAD on same resource
```

**Recommendation:** Option B (matches AWS S3 behavior).

**Do versioned operations need separate permissions?**

Option A: Separate permissions (AWS S3 pattern - recommended)

```cedar
permit(action == Raja::Action::"s3:GetObject", ...)
permit(action == Raja::Action::"s3:GetObjectVersion", ...)  // Explicit for versions
permit(action == Raja::Action::"s3:DeleteObjectVersion", ...)  // Explicit for versions
```

Option B: Base operations imply versioned operations

```cedar
permit(action == Raja::Action::"s3:GetObject", ...)
// Also allows GetObjectVersion on same resource
```

**Recommendation:** Option A (matches AWS S3 behavior). Versioned operations have different security implications:

- `DeleteObject` deletes the latest version (adds delete marker)
- `DeleteObjectVersion` permanently deletes a specific version
- These should be explicitly granted, not implied

**Reference:** AWS requires explicit `s3:GetObjectVersion` and `s3:DeleteObjectVersion` permissions for versioned buckets.

## Success Criteria

- [ ] All policies use hierarchical syntax (no embedded bucket/key)
- [ ] No `*` wildcards in policy files
- [ ] Cedar parser extracts bucket and key components separately
- [ ] Compiler generates scopes with both components
- [ ] Lua enforcer implements prefix matching correctly
- [ ] All integration tests pass through Envoy
- [ ] Python `/authorize` endpoint removed
- [ ] Multipart upload operations work end-to-end
- [ ] Authorization decisions logged with full context (granted scope, requested scope, decision)

## Migration Path

1. **Update Cedar schema** - Add multipart actions
2. **Update parser** - Support hierarchical syntax
3. **Update compiler** - Generate new scope format
4. **Update Lua enforcer** - Implement prefix matching + S3 action mapping
5. **Rewrite policy files** - Use hierarchical syntax
6. **Update integration tests** - Add multipart test cases
7. **Remove Python authorizer** - Delete `/authorize` endpoint
8. **Deploy and test** - Full end-to-end validation
9. **Document** - Update README with new syntax and examples

## Non-Goals (Deferred to Future)

- Template expansion (`{{account}}`, `{{region}}`) - Compiler feature for later
- AVP description extraction - Policy metadata for later
- Role-based principals - MVP uses only `User`
- Policy validation in CI - Ensure policies compile successfully
- Scope optimization - Deduplicate or merge overlapping scopes
