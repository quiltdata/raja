# Prefix-Based Authorization Design: No Wildcards

## Executive Summary

This document proposes a redesigned authorization scheme that **eliminates wildcard characters** (`*`) entirely in favor of **pure prefix matching**. The goal is to make authorization checking a simple, efficient string prefix test with no pattern matching or special character handling.

## Problem Statement

The current gap analysis (01-rajee-gap.md) proposes using wildcards for scope matching:

```
S3:my-bucket/*:GetObject
S3:my-bucket/uploads/*:PutObject
```

**Issues with wildcards:**

1. **Complexity**: Requires pattern matching logic
2. **Ambiguity**: Multiple interpretations of `*` semantics
3. **Performance**: Pattern matching is slower than prefix testing
4. **Security**: Edge cases in wildcard handling can create vulnerabilities
5. **Cognitive Load**: Users must understand wildcard semantics

## Design Principle

> **Authorization is a prefix test, nothing more.**

If authorization can be reduced to:

```python
request.startswith(grant)
```

Then it becomes:

- **Trivially correct** (no edge cases)
- **Maximally fast** (O(1) string comparison)
- **Perfectly transparent** (obvious what's allowed)

## Proposed Design

### 1. Scope Format Restructuring

**Authority Format** (what a token grants):

```
{action}/{bucket}/{prefix}
```

**Request Format** (what operation is requested):

```
{action}/{bucket}/{key}
```

**Key insight:** By putting the action first, we enforce **exact action matching** before checking resource paths.

### 2. Authorization Algorithm

```python
def is_authorized(request: str, grants: list[str]) -> bool:
    """Check if request is covered by any grant using prefix matching."""
    for grant in grants:
        if request.startswith(grant):
            return True
    return False
```

That's it. No wildcards, no pattern matching, no special cases.

### 3. Examples

#### Example 1: Read-only access to entire bucket

**Grant:** `s3:GetObject/my-bucket/`

**Allowed:**

- `s3:GetObject/my-bucket/document.txt` ✅
- `s3:GetObject/my-bucket/subfolder/file.csv` ✅
- `s3:GetObject/my-bucket/deeply/nested/path/data.json` ✅

**Denied:**

- `s3:PutObject/my-bucket/document.txt` ❌ (action mismatch)
- `s3:GetObject/other-bucket/document.txt` ❌ (bucket mismatch)

#### Example 2: Write access to specific prefix

**Grant:** `s3:PutObject/my-bucket/uploads/`

**Allowed:**

- `s3:PutObject/my-bucket/uploads/file.txt` ✅
- `s3:PutObject/my-bucket/uploads/user123/avatar.png` ✅

**Denied:**

- `s3:PutObject/my-bucket/documents/file.txt` ❌ (prefix mismatch)
- `s3:PutObject/my-bucket/upload/file.txt` ❌ (prefix mismatch - note "upload" vs "uploads")
- `s3:GetObject/my-bucket/uploads/file.txt` ❌ (action mismatch)

#### Example 3: Multiple grants

**Grants:**

```
s3:GetObject/my-bucket/
s3:PutObject/my-bucket/uploads/
s3:DeleteObject/my-bucket/uploads/
```

**Behavior:**

- Read anything in `my-bucket/` ✅
- Write only to `my-bucket/uploads/*` ✅
- Delete only from `my-bucket/uploads/*` ✅
- Write to `my-bucket/documents/` ❌

#### Example 4: Single-file access

**Grant:** `s3:GetObject/my-bucket/specific-file.txt`

**Allowed:**

- `s3:GetObject/my-bucket/specific-file.txt` ✅

**Denied:**

- `s3:GetObject/my-bucket/specific-file.txt.backup` ❌ (not exact match, no prefix)
- `s3:GetObject/my-bucket/other-file.txt` ❌

### 4. Trailing Slash Convention

**Important:** For prefix grants, always include trailing slash:

- `s3:GetObject/my-bucket/` - Grants access to all keys in bucket
- `s3:GetObject/my-bucket/uploads/` - Grants access to all keys under `uploads/`
- `s3:GetObject/my-bucket/doc.txt` - Grants access to exactly `doc.txt`

**Without trailing slash:**

- `s3:GetObject/my-bucket/upload` would match:
  - `s3:GetObject/my-bucket/upload` ✅
  - `s3:GetObject/my-bucket/uploads/file.txt` ✅ (prefix match!)
  - This is usually NOT what you want

**With trailing slash:**

- `s3:GetObject/my-bucket/upload/` would match:
  - `s3:GetObject/my-bucket/upload/file.txt` ✅
  - `s3:GetObject/my-bucket/uploads/file.txt` ❌ (no match)

## Token Structure

### JWT Claims

```json
{
  "iss": "https://raja.example.com",
  "sub": "User::alice",
  "aud": "s3-api",
  "iat": 1234567890,
  "exp": 1234568190,
  "grants": [
    "s3:GetObject/my-bucket/",
    "s3:PutObject/my-bucket/uploads/",
    "s3:DeleteObject/my-bucket/uploads/"
  ]
}
```

**Key change:** Replace `scopes` with `grants` to reflect the new semantics.

### Token Issuance API

**Request:**

```http
POST /token
Content-Type: application/json

{
  "principal": "User::alice",
  "grants": [
    "s3:GetObject/my-bucket/",
    "s3:PutObject/my-bucket/uploads/"
  ]
}
```

**Response:**

```json
{
  "token": "eyJhbGc...",
  "expires_at": 1234568190,
  "grants": [
    "s3:GetObject/my-bucket/",
    "s3:PutObject/my-bucket/uploads/"
  ]
}
```

## Lambda Authorizer Flow

### 1. Extract Request

From API Gateway event:

```
Method: PUT
Path: /s3/my-bucket/uploads/file.txt
```

**Normalize to request string:**

```
s3:PutObject/my-bucket/uploads/file.txt
```

### 2. Extract Grants

From JWT token:

```python
payload = jwt.decode(token, secret, algorithms=["HS256"])
grants = payload["grants"]  # ["s3:PutObject/my-bucket/uploads/", ...]
```

### 3. Check Authorization

```python
request = "s3:PutObject/my-bucket/uploads/file.txt"

for grant in grants:
    if request.startswith(grant):
        return "ALLOW"

return "DENY"
```

**Result:** ALLOW (because `request.startswith("s3:PutObject/my-bucket/uploads/")` is True)

## Comparison with Wildcard Approach

| Aspect | Wildcard Approach | Prefix Approach |
|--------|------------------|-----------------|
| Grant format | `S3:bucket/*:GetObject` | `s3:GetObject/bucket/` |
| Authorization logic | Pattern matching | String prefix test |
| Lines of code | ~50 lines | ~5 lines |
| Performance | O(n×m) pattern matching | O(n) string prefix |
| Edge cases | Many (escape chars, nested wildcards) | None |
| Security review | Complex | Trivial |
| Transparency | Must understand wildcard rules | Must understand prefix rules |
| Failed grant example | `S3:bucket/doc*:GetObject` (what does this mean?) | N/A (no patterns) |

## No Backward Compatibility Required

**There are no external users yet.** We can completely replace the old format with the new prefix-based format. No migration code needed.

## Keep It Simple: No Optional Enhancements

**Do not support action prefix matching.** Keep actions explicit. If you need multiple actions, list them:

```json
{
  "grants": [
    "s3:GetObject/my-bucket/uploads/",
    "s3:PutObject/my-bucket/uploads/",
    "s3:DeleteObject/my-bucket/uploads/"
  ]
}
```

No wildcards. No patterns. No special cases. Just prefix matching.

## Implementation Considerations

### 1. Keep It Simple

**No normalization needed.** Grants are strings. Requests are strings. Authorization is prefix matching. That's it.

### 2. Minimal Validation

At token issuance, just check:

- Grant is non-empty
- Grant contains at least one `/`
- Action part (before first `/`) contains `:`

That's all you need.

### 3. Security Considerations

**Path traversal prevention:**

Requests are constructed from API Gateway path parameters, not user input. Example:

```python
# API Gateway extracts from URL path
bucket = path_params["bucket"]  # From /s3/{bucket}/{key+}
key = path_params["key"]

# Construct request
request = f"s3:{action}/{bucket}/{key}"
```

Since the bucket and key are extracted from the URL path structure (not query params or body), path traversal attacks like `../` are not applicable.

**Prefix confusion prevention:**

Always use trailing slashes for prefix grants:

- ✅ `s3:GetObject/bucket/uploads/` (clear prefix)
- ❌ `s3:GetObject/bucket/upload` (ambiguous - matches "upload" and "uploads")

### 4. Testing Strategy

Prefix matching is trivial to test:

```python
def test_prefix_authorization():
    grant = "s3:GetObject/bucket/uploads/"

    # Should allow
    assert "s3:GetObject/bucket/uploads/file.txt".startswith(grant)
    assert "s3:GetObject/bucket/uploads/deep/path.csv".startswith(grant)

    # Should deny
    assert not "s3:PutObject/bucket/uploads/file.txt".startswith(grant)
    assert not "s3:GetObject/bucket/other/file.txt".startswith(grant)
    assert not "s3:GetObject/other-bucket/uploads/file.txt".startswith(grant)
```

**Property-based tests** with Hypothesis:

```python
@given(
    action=st.sampled_from(["s3:GetObject", "s3:PutObject"]),
    bucket=st.text(min_size=1, max_size=63),
    prefix=st.text(min_size=0, max_size=100),
    key=st.text(min_size=1, max_size=100)
)
def test_prefix_property(action, bucket, prefix, key):
    """Property: request with prefix in path should match grant with that prefix."""
    grant = f"{action}/{bucket}/{prefix}"
    request = f"{action}/{bucket}/{prefix}{key}"

    # Request with prefix in path must match grant with that prefix
    assert request.startswith(grant)

    # Request with different action must not match
    other_action = "s3:PutObject" if action == "s3:GetObject" else "s3:GetObject"
    wrong_request = f"{other_action}/{bucket}/{prefix}{key}"
    assert not wrong_request.startswith(grant)
```

## Advantages

### 1. Simplicity

The entire authorization logic is:

```python
any(request.startswith(grant) for grant in grants)
```

This is **obviously correct** by inspection.

### 2. Performance

- **No parsing** of patterns or wildcards
- **No regex** or glob matching
- **Pure string prefix comparison** (O(n) where n = length of grant)
- Can be optimized with trie data structures if needed

### 3. Security

- **No special characters** to escape or sanitize
- **No pattern injection** attacks
- **No ambiguity** in matching semantics
- Easy to audit and review

### 4. Transparency

Users can trivially verify authorization:

> "Does my request string start with any of my grant strings?"

This is far easier to reason about than wildcard rules.

### 5. Composability

Multiple grants compose naturally:

```json
{
  "grants": [
    "s3:GetObject/public/",
    "s3:GetObject/shared/team-a/",
    "s3:PutObject/uploads/user-123/",
    "s3:DeleteObject/uploads/user-123/"
  ]
}
```

Each grant is independent and clear.

## Disadvantages and Mitigations

### 1. Verbosity

You must list each action explicitly. This is a **feature**, not a bug. Explicit permissions = better security.

### 2. Trailing Slash Convention

**Simple rule:**

- Prefix grants end with `/`: `s3:GetObject/bucket/uploads/`
- Exact key grants don't: `s3:GetObject/bucket/doc.txt`

Users must be explicit. No magic behavior.

### 3. Case Sensitivity

S3 keys are case-sensitive. Prefix matching is case-sensitive. This is correct.

## Why Not Follow IAM's Wildcard Approach?

IAM policies are evaluated server-side by AWS. RAJA tokens are bearer credentials evaluated in a Lambda authorizer. **Simpler is safer** for bearer tokens.

## Summary

1. **Use prefix matching** - No wildcards, no patterns
2. **Keep actions explicit** - List each action separately
3. **Use trailing slash** - Prefixes end with `/`, exact keys don't
4. **No backward compatibility** - There are no external users yet

## Implementation Checklist

- [ ] Replace token API: `scopes` → `grants`
- [ ] Update JWT claims: `scopes` → `grants`
- [ ] Lambda authorizer: construct request string from API Gateway event
- [ ] Lambda authorizer: implement `any(request.startswith(grant) for grant in grants)`
- [ ] Add unit tests for prefix matching
- [ ] Add integration tests for end-to-end flow
- [ ] Update documentation

## Conclusion

Authorization = `any(request.startswith(grant) for grant in grants)`

That's it. No wildcards. No patterns. No edge cases. Simple, fast, correct.
