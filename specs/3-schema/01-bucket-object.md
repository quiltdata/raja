# S3 Bucket and Object Hierarchical Schema

## Problem

The current approach embeds both bucket name and object key into a single resource identifier:

```cedar
resource == Raja::S3Object::"bucket-name/key/path"
```

This prevents independent prefix matching on bucket vs. key, making it impossible to express policies like:

- "All buckets starting with `raja-poc-test-`"
- "All objects under `rajee-integration/` prefix"

Using wildcards like `raja-poc-test-*/rajee-integration/*` is a **security violation** because the middle `*` matches unpredictably across both bucket and key components.

## Solution: Hierarchical Resource Structure

Cedar's schema already supports hierarchical relationships:

```cedar
entity S3Bucket {}
entity S3Object in [S3Bucket] {}
```

We leverage this to separate bucket and key components:

```cedar
resource == Raja::S3Object::"key-prefix" in Raja::S3Bucket::"bucket-prefix"
```

### Examples

**Object operations with prefix matching:**

```cedar
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);
```

This grants `test-user` access to:

- Any object with key starting with `rajee-integration/`
- In any bucket starting with `raja-poc-test-`

**Bucket operations:**

```cedar
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"raja-poc-test-"
);
```

This grants `test-user` access to list any bucket starting with `raja-poc-test-`.

## Prefix Matching Rules

### Does Cedar Distinguish Exact vs. Prefix Matching?

**No.** Cedar only supports exact string matching for entity identifiers. Cedar does not have built-in prefix matching.

This means RAJA must implement prefix matching in the **enforcer**, not rely on Cedar's native semantics.

### Our Convention: Trailing Indicator for Prefix Match

We use a trailing `/` or `-` to indicate a prefix match:

- `"rajee-integration/"` → prefix match for keys starting with `rajee-integration/`
- `"raja-poc-test-"` → prefix match for buckets starting with `raja-poc-test-`
- `"my-bucket"` → exact match for bucket named exactly `my-bucket`
- `"my-file.txt"` → exact match for key named exactly `my-file.txt`

The enforcer detects the trailing indicator and applies prefix logic accordingly.

### No Internal Wildcards

Internal `*` wildcards are **prohibited** as they create security violations:

- ❌ `"test-*/integration/*"` - unpredictable, insecure
- ✅ `"test-"` - clear prefix boundary
- ✅ `"integration/"` - clear prefix boundary

## Compilation to Scopes

The compiler translates hierarchical Cedar policies into RAJA scopes:

**Cedar policy:**

```cedar
resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
```

**Compiled scope:**

```
S3Object:raja-poc-test-/rajee-integration/:s3:GetObject
```

The scope format preserves both components: `ResourceType:bucket-prefix/key-prefix:action`

## Enforcement with Prefix Matching

The enforcer performs prefix matching on each component independently:

**Granted scope:**

```
S3Object:raja-poc-test-/rajee-integration/:s3:GetObject
```

**Requested access:**

```
S3Object:raja-poc-test-712023778557-us-east-1/rajee-integration/file.txt:s3:GetObject
```

**Match logic:**

1. Check bucket prefix: `raja-poc-test-712023778557-us-east-1`.startsWith(`raja-poc-test-`) ✅
2. Check key prefix: `rajee-integration/file.txt`.startsWith(`rajee-integration/`) ✅
3. Check action: `s3:GetObject` == `s3:GetObject` ✅
4. **Decision: ALLOW**

## Security Properties

1. **No internal wildcards** - eliminates ambiguous matching
2. **Explicit prefix boundaries** - clear, predictable semantics
3. **Independent component matching** - bucket and key evaluated separately
4. **Fail-closed by default** - unknown patterns deny by default
5. **Account/region agnostic** - prefixes work across deployments without hardcoding

## Template Support for Account/Region Expansion

### The Problem

Bucket names often include account ID and region:

- `raja-poc-test-712023778557-us-east-1`
- `raja-poc-test-123456789012-us-west-2`

Hardcoding these in policies makes them deployment-specific and brittle.

### Solution: Template Expansion in Compiler

The compiler should support template variables that expand at compilation time:

**Cedar policy with templates:**

```cedar
resource == Raja::S3Bucket::"raja-poc-test-{{account}}-{{region}}"
```

**Compiler expands to scope:**

```
S3Bucket:raja-poc-test-712023778557-us-east-1:s3:ListBucket
```

**When to use templates vs. prefixes:**

- Use **templates** for exact matching with dynamic components (account, region)
- Use **prefixes** for intentional pattern matching (key paths, bucket families)

**Template syntax:**

- `{{account}}` → AWS account ID
- `{{region}}` → AWS region
- `{{env}}` → Environment (dev, staging, prod)

Templates are resolved by the compiler using deployment context (from CDK outputs, environment variables, etc.).

## Principal Types: User vs. Role

### Current Schema

```cedar
entity User {}
entity Role {}
```

### Do We Need Both?

**Analysis:**

1. **User** represents individual identities (humans, service accounts)
2. **Role** represents groups or permission sets

**Recommendation:** Keep both for flexibility, but start simple:

- **MVP:** Use only `User` for all principals
- **Future:** Add `Role` support for group-based permissions when needed

**Migration path:**

```cedar
// Today: Individual users
principal == Raja::User::"alice"

// Future: Role-based access
principal in Raja::Role::"data-engineers"
```

For now, policies should only use `User`. The schema keeps `Role` for future extensibility.

## Linking Policies to Tests via AVP Descriptions

### The Challenge

AVP policy descriptions are currently blank. We could use them to:

1. Link policies to their corresponding test files
2. Document the purpose and context of each policy
3. Enable traceability between policies and tests

### Proposal: Structured Description Format

**Format:**

```
[Purpose] | [Test] | [Owner]
```

**Example:**

```
Grant test-user access to rajee-integration/ prefix | tests/integration/test_rajee_envoy_bucket.py::test_get_object_with_valid_token | @ernest
```

**Benefits:**

1. **Traceability:** Easily find which tests validate each policy
2. **Documentation:** Clear purpose statement
3. **Ownership:** Know who to contact about policy questions
4. **Automation:** Scripts can parse descriptions to verify test coverage

**Implementation:**

- Add description field when loading policies to AVP
- Update `scripts/load_policies.py` to extract from Cedar comments
- Add description validation in CI

**Cedar comment convention:**

```cedar
// @description Grant test-user access to rajee-integration/ prefix
// @test tests/integration/test_rajee_envoy_bucket.py::test_get_object_with_valid_token
// @owner @ernest
permit(
  principal == Raja::User::"test-user",
  action == Raja::Action::"s3:GetObject",
  resource == Raja::S3Object::"rajee-integration/" in Raja::S3Bucket::"raja-poc-test-"
);
```

## Migration

Existing policies using embedded identifiers:

```cedar
resource == Raja::S3Object::"bucket/key"
```

Must be rewritten to hierarchical form:

```cedar
resource == Raja::S3Object::"key" in Raja::S3Bucket::"bucket"
```

This requires updates to:

- Cedar policy files (use hierarchical syntax)
- Policy compiler (parse `in` syntax, expand templates, extract descriptions)
- Scope format (represent both bucket and key components)
- Enforcer (implement prefix matching logic with trailing indicator detection)
- AVP loading script (populate descriptions from Cedar comments)
