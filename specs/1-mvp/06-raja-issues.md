# RAJA MVP Known Issues and Blockers

## Status: Post CDK Deployment Testing

**Date**: 2026-01-13
**Context**: Issues discovered during `./poe cdk-deploy` and integration testing

---

## Issue 1: Lambda Bundling Does Not Include Local `raja` Package

**Status**: 🔴 **BLOCKING** - Integration tests cannot pass until resolved

**Description**:

The Lambda functions (TokenService, EnforcerLambda, CompilerLambda, IntrospectLambda) all fail with `Runtime.ImportModuleError: Unable to import module 'handler': No module named 'raja'` when invoked.

**Root Cause**:

- Lambda handlers import the local `raja` package: `from raja import create_token`
- Lambda constructs use `lambda_.Code.from_asset("../lambda_handlers/token_service")`
- This bundles only the files in the handler directory, not the `raja` package from `src/raja/`
- The `requirements.txt` includes `raja` but CDK doesn't know how to install a local package

**Evidence**:

```text
[ERROR] Runtime.ImportModuleError: Unable to import module 'handler': No module named 'raja'
Traceback (most recent call last):
```

**Files Affected**:

- `infra/raja_poc/constructs/token_service.py:26`
- `infra/raja_poc/constructs/enforcer_lambda.py:26`
- `infra/raja_poc/constructs/compiler_lambda.py:26`
- `infra/raja_poc/constructs/introspect_lambda.py` (assumed similar)

**Solution Options**:

### Option 1: Use PythonFunction with Local Bundling (Recommended)

- Replace `lambda_.Function` with `aws_lambda_python_alpha.PythonFunction`
- Add `entry` pointing to handler directory
- Add `index` specifying handler file
- Install local package in bundling:

```python
from aws_cdk import aws_lambda_python_alpha as lambda_python

self.function = lambda_python.PythonFunction(
    self,
    "Function",
    runtime=lambda_.Runtime.PYTHON_3_12,
    entry="../lambda_handlers/token_service",
    index="handler.py",
    handler="lambda_handler",
    bundling=lambda_python.BundlingOptions(
        asset_excludes=[".venv", "__pycache__", "*.pyc"],
        # Copy raja package into bundle
        command_hooks=lambda_python.ICommandHooks(
            before_bundling=lambda input_dir, output_dir: [
                f"cp -r {input_dir}/../src/raja {output_dir}/raja"
            ]
        )
    )
)
```

### Option 2: Create Lambda Layer with `raja` Package

- Build a Lambda layer containing the `raja` package
- Attach layer to all Lambda functions
- Update `requirements.txt` to not reference `raja`

### Option 3: Bundle `raja` Package Manually in Asset

- Create a packaging script that copies `raja` into each handler directory
- Run during CDK synth/deploy
- Less elegant but works with current setup

**Impact on CI**:

- Integration tests (`./poe test-integration`) will fail until fixed
- CDK deployment succeeds but Lambda invocations fail
- Health endpoint works (uses inline code) but all other endpoints fail with 502

**Priority**: HIGH - Blocks integration testing and hypothesis validation

---

## Issue 2: Integration Tests Missing AWS Region Configuration

**Status**: 🟡 **MINOR** - One test affected

**Description**:

The `test_policy_store_lists_policies` test fails with `botocore.exceptions.NoRegionError: You must specify a region` when creating a boto3 client.

**Root Cause**:

```python
client = boto3.client("verifiedpermissions")  # No region specified
```

**Solution**:

Add region parameter or set `AWS_DEFAULT_REGION` environment variable:

```python
region = os.environ.get("AWS_REGION", "us-east-1")
client = boto3.client("verifiedpermissions", region_name=region)
```

**Files Affected**:

- `tests/integration/test_policy_store.py:13`

**Impact on CI**:

- One integration test fails unnecessarily
- Easy fix, low priority

---

## Issue 3: Integration Tests Have Real Implementations

**Status**: ✅ **GOOD NEWS** - Tests are properly implemented

**Description**:

The integration tests have been updated with real implementations that properly test the deployed infrastructure:

**Current Test Quality**:

- ✅ `test_end_to_end_token_authorize_introspect` - Real test, fails due to Issue #1
- ✅ `test_enforcement_allows_granted_scope` - Real test, fails due to Issue #1
- ✅ `test_enforcement_denies_missing_scope` - Real test, fails due to Issue #1
- ✅ `test_enforcement_allows_admin_delete` - Real test, fails due to Issue #1
- ⚠️  `test_policy_store_lists_policies` - Real test, fails due to Issue #2
- ✅ `test_token_service_issues_token_for_known_principal` - Real test, fails due to Issue #1
- ✅ `test_token_service_rejects_unknown_principal` - Real test, fails due to Issue #1

**Note**: Integration tests have been updated with real implementations. Once Issue #1 is resolved, these tests will provide proper validation.

**Impact on CI**:

- Once Issue #1 is resolved, tests will provide real validation
- No false positives

---

## Issue 4: Missing Test Data Setup

**Status**: 🟡 **REQUIRED** - Tests expect data that doesn't exist

**Description**:

Integration tests expect principals to exist in DynamoDB:

- Test expects `alice` principal with scopes: `["Document:doc123:read", "Document:doc123:write"]`
- Test expects `admin` principal with scopes: `["Document:doc123:delete"]`
- No mechanism to populate this test data before tests run

**Solution**:

Add test data setup to integration workflow:

```yaml
- name: Seed Test Data
  run: |
    aws dynamodb put-item \
      --region us-east-1 \
      --table-name $PRINCIPAL_TABLE \
      --item '{"principal_id": {"S": "alice"}, "scopes": {"L": [{"S": "Document:doc123:read"}, {"S": "Document:doc123:write"}]}}'

    aws dynamodb put-item \
      --region us-east-1 \
      --table-name $PRINCIPAL_TABLE \
      --item '{"principal_id": {"S": "admin"}, "scopes": {"L": [{"S": "Document:doc123:delete"}]}}'
```

Or create a poe task:

```bash
./poe seed-test-data
```

**Files to Create**:

- `scripts/seed_test_data.py` - Script to populate DynamoDB with test principals
- Update `pyproject.toml` with `seed-test-data` task

**Impact on CI**:

- Integration tests will fail even after Issue #1 is fixed
- Required for end-to-end testing

---

## Issue 5: CDK Synthesis Directory Left in Git Status

**Status**: 🟢 **COSMETIC** - No functional impact

**Description**:

After running `./poe cdk-deploy`, the following untracked files appear:

```text
?? cdk.context.json
?? infra/cdk.context.json
?? infra/cdk.out/
```

**Solution**:

Add to `.gitignore`:

```gitignore
cdk.context.json
cdk.out/
```

**Impact on CI**:

- None - CI runs in ephemeral environments
- Local development convenience

---

## Resolution Checklist

Before integration tests can pass in CI:

- [ ] **Fix Issue #1**: Lambda bundling includes `raja` package
- [ ] **Fix Issue #2**: Add region to boto3 client in policy store test
- [ ] **Fix Issue #4**: Create test data seeding mechanism
- [ ] **Verify Issue #3**: Confirm real integration tests pass end-to-end
- [ ] **Fix Issue #5**: Add CDK artifacts to `.gitignore`

**Estimated Effort**:

- Issue #1: 2-3 hours (implement PythonFunction or Layer)
- Issue #2: 5 minutes
- Issue #4: 30-60 minutes (create seed script + poe task)
- Issue #3: N/A (tests already implemented - validation only)
- Issue #5: 1 minute

**Total**: ~3-4 hours to unblock integration testing

---

## Testing Strategy After Resolution

Once Issue #1 is resolved, run full integration test suite:

```bash
# Export stack outputs
export RAJA_API_URL=https://7tp2ch1qoj.execute-api.us-east-1.amazonaws.com/prod/
export POLICY_STORE_ID=EpdiGSK8Ht2Cnnjxz2UtEd
export AWS_REGION=us-east-1

# Seed test data
./poe seed-test-data

# Run integration tests
./poe test-integration -v
```

**Expected Results**:

- All 7 integration tests pass
- Token service issues valid JWTs
- Authorization service correctly allows/denies based on scopes
- Policy store contains 3 policies
- Introspection returns valid claims

---

## Deployment Summary

**Successfully Deployed**:

- ✅ RajaAvpStack: PolicyStore with 3 policies (document_read, document_write, admin_full)
- ✅ RajaServicesStack: API Gateway + 4 Lambda functions + DynamoDB tables
- ✅ API URL: `https://7tp2ch1qoj.execute-api.us-east-1.amazonaws.com/prod/`
- ✅ Health endpoint working: `/health` returns "ok"

**Deployment Issues Fixed**:

1. ✅ PolicyStore Schema Format - Converted Cedar to JSON format for AWS AVP
2. ✅ Namespace Requirement - Added `"Raja"` namespace (was empty string)
3. ✅ Policy Qualification - Updated all Cedar policies to use namespace-qualified names:
   - `policies/policies/document_read.cedar`
   - `policies/policies/document_write.cedar`
   - `policies/policies/admin_full.cedar`
4. ✅ Health Lambda Handler - Fixed handler name from `handler.lambda_handler` to `index.lambda_handler` for inline code

---

## Next Steps

### Immediate Priority

1. **Fix Issue #1** (Lambda bundling) - BLOCKING
   - Implement PythonFunction with local package bundling OR
   - Create Lambda layer with `raja` package

### Quick Wins

2. **Fix Issue #2** (region config) - 5 minutes
   - Add `region_name` parameter to boto3 client

3. **Fix Issue #5** (gitignore) - 1 minute
   - Add CDK artifacts to `.gitignore`

### Required for Testing

4. **Implement Issue #4** (test data seeding)
   - Create `scripts/seed_test_data.py`
   - Add `seed-test-data` poe task
   - Document DynamoDB table schema for principals

### Verification

5. **Verify Full Integration Test Suite**
   - Run all tests with proper environment variables
   - Confirm all 7 tests pass
   - Validate API endpoints work end-to-end

### Then Proceed With

6. **CI/CD Implementation** per [05-ci-spec.md](05-ci-spec.md)
   - Create composite setup action
   - Implement CI workflow
   - Add integration workflow
   - Setup AWS OIDC and environments

---

## References

- **CI/CD Spec**: [05-ci-spec.md](05-ci-spec.md)
- **Poe Tasks**: [04-poe-tasks.md](04-poe-tasks.md)
- **CDK Stack Outputs**:
  - PolicyStore ID: `EpdiGSK8Ht2Cnnjxz2UtEd`
  - API Gateway URL: `https://7tp2ch1qoj.execute-api.us-east-1.amazonaws.com/prod/`
  - Stack ARNs available in CloudFormation console
