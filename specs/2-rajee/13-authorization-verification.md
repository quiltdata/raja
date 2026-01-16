# RAJEE Authorization Verification - Real Authorization Testing

## Critical Discovery

**Current tests pass due to `RAJEE_PUBLIC_GRANTS` bypass, NOT real authorization!**

Investigation reveals:
- Alice's token contains **0 grants** (verified by decoding JWT)
- Tests pass because `RAJEE_PUBLIC_GRANTS` env var hardcoded in CDK stack bypasses JWT entirely
- Public grants allow: `s3:*/{bucket}/rajee-integration/` without ANY token validation
- **We're not actually testing authorization - we're testing the public bypass!**

## Root Cause Analysis

### 1. Missing Cedar Policies

Alice has policies for `analytics-data` bucket:
```cedar
// policies/data_analyst_read.cedar
permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:GetObject",
  resource in Raja::S3Bucket::"analytics-data"
);
```

But tests use a **different bucket**: `raja-poc-test-{account}-{region}`

Result: No policies match → No scopes compiled → **0 grants in token**

### 2. Public Grants Bypass

CDK stack hardcodes public access at [infra/raja_poc/stacks/rajee_envoy_stack.py:87-94](../../infra/raja_poc/stacks/rajee_envoy_stack.py#L87-L94):

```python
public_grants = [
    f"s3:GetObject/{test_bucket.bucket_name}/rajee-integration/",
    f"s3:PutObject/{test_bucket.bucket_name}/rajee-integration/",
    f"s3:DeleteObject/{test_bucket.bucket_name}/rajee-integration/",
    # ... etc
]
```

Lua filter checks public grants **before** JWT:
```lua
-- authorize.lua line 97-108
if #public_grants > 0 then
  local public_allowed, public_reason = auth_lib.authorize(public_grants, request_string)
  if public_allowed then
    return  -- Allow without checking JWT!
  end
end
```

### 3. Why Tests Pass Anyway

- ✅ `rajee-integration/*` requests → Match public grants → **ALLOW (no JWT needed)**
- ✅ `unauthorized-prefix/*` requests → No public grant match → Check JWT → **0 grants → DENY (403)**

Both behaviors work, but neither actually tests real authorization!

## The Real Goal

We need to:
1. **Create Cedar policies** that grant Alice access to the test bucket's `rajee-integration/*` prefix
2. **Remove or disable public grants bypass** for testing
3. **Verify tokens contain actual grants** from compiled policies
4. **Confirm authorization works** via grants, not public bypass

## Implementation Plan (Revised)

### Phase 0: Create Cedar Policy for Test Bucket

**Objective**: Give Alice real grants for the test bucket prefix.

**File**: `policies/rajee_integration_test.cedar` (NEW)

```cedar
// RAJEE Integration Test Policy
// Grants Alice full access to rajee-integration/* prefix in test bucket

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:GetObject",
  resource in Raja::S3Bucket::"raja-poc-test"
) when {
  resource.key.startsWith("rajee-integration/")
};

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:PutObject",
  resource in Raja::S3Bucket::"raja-poc-test"
) when {
  resource.key.startsWith("rajee-integration/")
};

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:DeleteObject",
  resource in Raja::S3Bucket::"raja-poc-test"
) when {
  resource.key.startsWith("rajee-integration/")
};

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:ListBucket",
  resource == Raja::S3Bucket::"raja-poc-test"
);

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:GetObjectAttributes",
  resource in Raja::S3Bucket::"raja-poc-test"
) when {
  resource.key.startsWith("rajee-integration/")
};

permit(
  principal == Raja::User::"alice",
  action == Raja::Action::"s3:ListObjectVersions",
  resource in Raja::S3Bucket::"raja-poc-test"
) when {
  resource.key.startsWith("rajee-integration/")
};
```

**Note**: Cedar policies compile to scopes like:
- `S3Object:raja-poc-test/rajee-integration/:s3:GetObject`

Which convert to grants:
- `s3:GetObject/raja-poc-test/rajee-integration/`

### Phase 1: Remove Public Grants Bypass (Test-Only)

**Objective**: Disable public grants for testing to force real authorization.

**Option A**: Add CDK parameter to disable public grants

**File**: [infra/raja_poc/stacks/rajee_envoy_stack.py](../../infra/raja_poc/stacks/rajee_envoy_stack.py)

```python
# Add parameter (around line 30)
use_public_grants = CfnParameter(
    self,
    "UsePublicGrants",
    type="String",
    description="Enable public grants bypass for test prefix",
    default="false",
    allowed_values=["true", "false"],
)

# Use parameter (around line 87)
public_grants = []
if use_public_grants.value_as_string == "true":
    public_prefix = "rajee-integration/"
    public_grants = [
        f"s3:GetObject/{test_bucket.bucket_name}/{public_prefix}",
        # ... etc
    ]
```

**Option B**: Use test-only override environment variable

Keep public grants by default, but add test that explicitly checks WITHOUT them:

```python
@pytest.mark.integration
def test_rajee_envoy_auth_with_real_grants() -> None:
    """Test authorization using real grants from Cedar policies, not public bypass.

    This test verifies that:
    1. Alice's token contains actual grants from compiled Cedar policies
    2. Authorization works via grant matching, not public bypass
    3. The complete policy → scope → grant → authorization flow works
    """
    # Get token and verify it has grants
    token = issue_rajee_token("alice")

    import jwt
    decoded = jwt.decode(token, options={"verify_signature": False})
    grants = decoded.get("grants", [])

    # CRITICAL: Token must have grants, not rely on public bypass
    assert len(grants) > 0, "Token has no grants! Check Cedar policies and compilation."

    print(f"\n🔑 Token has {len(grants)} grant(s) from compiled Cedar policies:")
    for grant in grants[:5]:
        print(f"   - {grant}")

    # Test operations
    bucket = require_rajee_test_bucket()
    s3, _, _ = _create_s3_client_with_rajee_proxy(verbose=True, token=token)

    key = f"rajee-integration/{uuid.uuid4().hex}.txt"
    body = b"real-authorization-test"

    # PUT should succeed via grant matching
    response = s3.put_object(Bucket=bucket, Key=key, Body=body)
    assert response["ResponseMetadata"]["HTTPStatusCode"] == 200
    print(f"✅ PUT succeeded via grant matching")

    # GET should succeed
    get_response = s3.get_object(Bucket=bucket, Key=key)
    assert get_response["Body"].read() == body
    print(f"✅ GET succeeded via grant matching")

    # Cleanup
    s3.delete_object(Bucket=bucket, Key=key)
    print(f"✅ DELETE succeeded via grant matching")
```

### Phase 2: Load Policies and Compile

**Objective**: Ensure new policies are loaded and compiled.

**Steps**:
```bash
# 1. Load new Cedar policy to AVP
./poe load-policies

# 2. Trigger compilation
./poe compile-policies

# 3. Verify Alice has scopes
python -c "
import boto3
from raja.server.dependencies import get_principal_table

table = get_principal_table()
response = table.get_item(Key={'principal': 'alice'})
scopes = response.get('Item', {}).get('scopes', [])
print(f'Alice has {len(scopes)} scopes:')
for scope in scopes[:10]:
    print(f'  - {scope}')
"
```

### Phase 3: Verify Token Has Grants

**Objective**: Confirm token issuance produces grants from compiled policies.

**Test**:
```bash
python -c "
import jwt
from tests.integration.helpers import issue_rajee_token

token = issue_rajee_token('alice')
decoded = jwt.decode(token, options={'verify_signature': False})
grants = decoded.get('grants', [])

print(f'✅ Token has {len(grants)} grants')
assert len(grants) > 0, 'ERROR: Token has no grants!'

for grant in grants[:10]:
    print(f'  - {grant}')
"
```

**Expected Output**:
```
✅ Token has 6 grants
  - s3:GetObject/raja-poc-test-712023778557-us-east-1/rajee-integration/
  - s3:PutObject/raja-poc-test-712023778557-us-east-1/rajee-integration/
  - s3:DeleteObject/raja-poc-test-712023778557-us-east-1/rajee-integration/
  - s3:ListBucket/raja-poc-test-712023778557-us-east-1/
  - s3:GetObjectAttributes/raja-poc-test-712023778557-us-east-1/rajee-integration/
  - s3:ListObjectVersions/raja-poc-test-712023778557-us-east-1/rajee-integration/
```

### Phase 4: Add Test for Real Authorization

**File**: [tests/integration/test_rajee_envoy_bucket.py](../../tests/integration/test_rajee_envoy_bucket.py)

Add test from Phase 1 Option B above.

### Phase 5: Run Demo and Verify

**Objective**: Confirm all tests pass with real authorization.

```bash
./poe demo
```

**Expected**: All 7 tests pass, including new `test_rajee_envoy_auth_with_real_grants`

## Decision: Public Grants vs Real Authorization

We have two options:

### Option A: Keep Public Grants (Current)
**Pros**:
- Simple for testing
- No policy management needed
- Fast test setup

**Cons**:
- ❌ Not testing real authorization
- ❌ Doesn't validate policy → scope → grant flow
- ❌ Hides bugs in compilation/token issuance
- ❌ False confidence in production readiness

### Option B: Use Real Authorization (Recommended)
**Pros**:
- ✅ Tests complete authorization flow
- ✅ Validates Cedar policies work correctly
- ✅ Validates compilation produces correct scopes
- ✅ Validates token issuance includes grants
- ✅ Real-world confidence

**Cons**:
- Requires Cedar policy management
- Requires policy compilation step
- More complex setup

**Recommendation**: Use **Option B** for integration tests. Public grants can remain for manual testing/demos.

## Critical Files

### Files to Create
1. **policies/rajee_integration_test.cedar** (NEW)
   - Cedar policies granting Alice access to test bucket

### Files to Modify
2. **[tests/integration/test_rajee_envoy_bucket.py](../../tests/integration/test_rajee_envoy_bucket.py)**
   - Add `test_rajee_envoy_auth_with_real_grants()` test
   - Enhance logging to show grant count

3. **[infra/raja_poc/stacks/rajee_envoy_stack.py](../../infra/raja_poc/stacks/rajee_envoy_stack.py)** (Optional)
   - Add parameter to disable public grants for testing

## Success Criteria

✅ **Cedar policies** created for test bucket prefix
✅ **Policies loaded** to AVP and compiled
✅ **Alice's token** contains 6+ grants (not 0!)
✅ **New test** passes using real grant matching
✅ **All existing tests** still pass
✅ **`./poe demo`** shows grants in output

## Timeline

- **Phase 0** (Cedar policies): 30 minutes
- **Phase 1** (Remove bypass): 20 minutes
- **Phase 2** (Load & compile): 10 minutes
- **Phase 3** (Verify grants): 10 minutes
- **Phase 4** (Add test): 30 minutes
- **Phase 5** (Run demo): 10 minutes

**Total: ~2 hours**

## Rollback

If issues occur:
- Cedar policies can be removed
- Public grants continue to work
- New test can be skipped
- No infrastructure changes required if using Option 1B
