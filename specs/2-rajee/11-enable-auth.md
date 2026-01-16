# Enable Authentication in RAJEE Envoy Stack

## Goal

Enable JWT authentication in the RAJEE Envoy stack by default (fail-closed mode) with proper integration testing that validates authorization constraints work before integrating with RAJA policies.

## Current State

- Envoy Lua authorization implementation is complete ([09-envoy-mechanistic-authorization-plan.md](09-envoy-mechanistic-authorization-plan.md))
- Integration tests exist but assume `AUTH_DISABLED=true` ([tests/integration/test_rajee_envoy_bucket.py](../../tests/integration/test_rajee_envoy_bucket.py))
- Tests use hard-coded prefix `rajee-integration/` for all S3 operations
- Current default: Authorization DISABLED (`AUTH_DISABLED=true` in Docker/entrypoint)
- CDK parameter already defaults to `false`, but Docker overrides it

## Strategy

Follow a safe, incremental approach:

1. **Add always-allow policy** for the test prefix
2. **Add negative test** that validates auth denies writes outside the allowed prefix (will fail initially)
3. **Enable auth by default** in all environments
4. **Deploy and verify** all tests pass

## Implementation Steps

### Step 1: Add Always-Allow Policy for Test Prefix

Create a Cedar policy that grants full access to the `rajee-integration/` prefix used by integration tests.

**File to modify:** [policies/policies/rajee-test-policy.cedar](../../policies/policies/rajee-test-policy.cedar) (new file)

```cedar
// RAJEE Integration Test Policy
// Grants full access to the test prefix for integration testing

// Allow all operations under rajee-integration/ prefix
permit (
    principal == User::"test-user",
    action in [
        Action::"s3:GetObject",
        Action::"s3:PutObject",
        Action::"s3:DeleteObject",
        Action::"s3:ListBucket",
        Action::"s3:GetObjectAttributes",
        Action::"s3:ListObjectVersions"
    ],
    resource
)
when {
    resource.bucket == "raja-poc-test" &&
    resource.key.startsWith("rajee-integration/")
};
```

**Token generation:** Update test helper to issue tokens with appropriate grants.

**File to modify:** [tests/integration/helpers.py](../../tests/integration/helpers.py)

Add a new helper function:

```python
def issue_rajee_token(bucket: str, prefix: str = "rajee-integration/") -> str:
    """Issue a RAJEE token with grants for the test bucket/prefix.

    Args:
        bucket: S3 bucket name
        prefix: S3 key prefix to grant access to

    Returns:
        JWT token with grants claim
    """
    import jwt
    import time
    from datetime import datetime, timedelta

    # Get JWT secret (same as control plane)
    secret = os.environ.get("JWT_SECRET", "test-secret-key-for-local-testing")

    # Create grants for the test prefix
    grants = [
        f"s3:GetObject/{bucket}/{prefix}",
        f"s3:PutObject/{bucket}/{prefix}",
        f"s3:DeleteObject/{bucket}/{prefix}",
        f"s3:ListBucket/{bucket}/{prefix}",
        f"s3:GetObjectAttributes/{bucket}/{prefix}",
        f"s3:ListObjectVersions/{bucket}/{prefix}",
    ]

    # Create token
    now = datetime.utcnow()
    payload = {
        "sub": "User::test-user",
        "iss": "https://test.local",
        "aud": ["raja-s3-proxy"],
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "iat": int(now.timestamp()),
        "grants": grants,
    }

    token = jwt.encode(payload, secret, algorithm="HS256")
    return token
```

### Step 2: Add Negative Authorization Test

Add a test that attempts to write OUTSIDE the allowed prefix and expects a 403 Forbidden response.

**File to modify:** [tests/integration/test_rajee_envoy_bucket.py](../../tests/integration/test_rajee_envoy_bucket.py)

```python
@pytest.mark.integration
def test_rajee_envoy_auth_denies_unauthorized_prefix(rajee_envoy_url: str, rajee_test_bucket: str) -> None:
    """Test that authorization denies access outside the allowed prefix.

    This test validates that the Envoy Lua authorization filter correctly
    denies requests that fall outside the granted scope, even with a valid JWT.

    Expected behavior:
    - Token grants access to 'rajee-integration/*'
    - Attempt to PUT to 'unauthorized-prefix/test.txt'
    - Should receive 403 Forbidden (not 401 - token is valid, just insufficient scope)
    """
    # Get token with grants for rajee-integration/ prefix only
    token = issue_rajee_token(rajee_test_bucket, prefix="rajee-integration/")

    # Attempt to write to a different prefix (should be denied)
    test_key = "unauthorized-prefix/test.txt"
    test_content = b"This should be denied"

    logger.info(f"Attempting unauthorized PUT to {test_key} (should fail with 403)")

    # Configure S3 client to use Envoy proxy with JWT
    s3 = boto3.client(
        "s3",
        endpoint_url=rajee_envoy_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(
            signature_version=UNSIGNED,
            s3={"addressing_style": "path"},
        ),
    )

    # Add Authorization header via event system
    def add_auth_header(request, **kwargs):
        request.headers.add_header("Authorization", f"Bearer {token}")

    s3.meta.events.register("before-sign.s3.*", add_auth_header)

    # Attempt PUT - should get 403 Forbidden
    with pytest.raises(ClientError) as exc_info:
        s3.put_object(
            Bucket=rajee_test_bucket,
            Key=test_key,
            Body=test_content,
        )

    # Validate error response
    error = exc_info.value.response["Error"]
    assert error["Code"] == "403", f"Expected 403 Forbidden, got {error['Code']}"
    assert "Forbidden" in error["Message"] or "Insufficient grants" in error["Message"]

    logger.info("✓ Unauthorized PUT correctly denied with 403 Forbidden")
```

**Expected behavior at this stage:**

- When `AUTH_DISABLED=true`: Test will FAIL (no auth checks, PUT succeeds)
- When `AUTH_DISABLED=false`: Test will PASS (auth denies unauthorized PUT)

### Step 3: Update Existing Tests to Use JWT Tokens

Modify existing integration tests to obtain and use JWT tokens.

**File to modify:** [tests/integration/test_rajee_envoy_bucket.py](../../tests/integration/test_rajee_envoy_bucket.py)

Update the main roundtrip test:

```python
@pytest.mark.integration
def test_rajee_envoy_s3_roundtrip_with_auth(rajee_envoy_url: str, rajee_test_bucket: str) -> None:
    """Test S3 operations through RAJEE Envoy proxy WITH authentication enabled.

    This is the core functionality test with JWT authorization.
    Tests: PUT → GET → DELETE with proper JWT token.
    """
    # Get token with grants for test prefix
    token = issue_rajee_token(rajee_test_bucket, prefix="rajee-integration/")

    # Generate unique test key
    test_key = f"rajee-integration/{uuid.uuid4()}.txt"
    test_content = b"Hello, RAJEE with auth!"

    # Configure S3 client with Envoy proxy endpoint
    s3 = boto3.client(
        "s3",
        endpoint_url=rajee_envoy_url,
        aws_access_key_id="test",
        aws_secret_access_key="test",
        config=Config(
            signature_version=UNSIGNED,
            s3={"addressing_style": "path"},
        ),
    )

    # Add Authorization header to all requests
    def add_auth_header(request, **kwargs):
        request.headers.add_header("Authorization", f"Bearer {token}")

    s3.meta.events.register("before-sign.s3.*", add_auth_header)

    # Rest of test remains the same (PUT → GET → DELETE)
    # ... existing test logic ...
```

**Apply the same pattern to:**

- `test_rajee_envoy_list_bucket()` - Add JWT token
- `test_rajee_envoy_get_object_attributes()` - Add JWT token
- `test_rajee_envoy_versioning_operations()` - Add JWT token

**Rename the old test:**

- Rename `test_rajee_envoy_s3_roundtrip_auth_disabled()` to `test_rajee_envoy_s3_roundtrip_auth_disabled_legacy()`
- Add `@pytest.mark.skip(reason="Legacy test, auth now enabled by default")` decorator

### Step 4: Enable Auth by Default in All Environments

Update defaults to fail-closed (authorization enabled) across the stack.

**Files to modify:**

1. **[infra/raja_poc/assets/envoy/Dockerfile](../../infra/raja_poc/assets/envoy/Dockerfile)** (line 13)

   ```dockerfile
   # Before:
   ENV AUTH_DISABLED=true

   # After:
   ENV AUTH_DISABLED=false
   ```

2. **[infra/raja_poc/assets/envoy/entrypoint.sh](../../infra/raja_poc/assets/envoy/entrypoint.sh)** (line 4)

   ```bash
   # Before:
   AUTH_DISABLED_VALUE="${AUTH_DISABLED:-true}"

   # After:
   AUTH_DISABLED_VALUE="${AUTH_DISABLED:-false}"
   ```

3. **[infra/docker-compose.yml](../../infra/docker-compose.yml)** (line 11)

   ```yaml
   # Before:
   environment:
     - AUTH_DISABLED=true

   # After:
   environment:
     - AUTH_DISABLED=false
   ```

4. **CDK Stack** (already correct at [infra/raja_poc/stacks/rajee_envoy_stack.py](../../infra/raja_poc/stacks/rajee_envoy_stack.py) line 117)
   - Default is already `"false"` ✅
   - No change needed

### Step 5: Update Environment Configuration for Tests

Ensure integration tests have access to the JWT secret.

**File to modify:** [tests/integration/helpers.py](../../tests/integration/helpers.py)

```python
def get_jwt_secret() -> str:
    """Get JWT signing secret for test token generation.

    Priority:
    1. JWT_SECRET environment variable
    2. Fetch from Secrets Manager (if AWS credentials available)
    3. Fall back to test default
    """
    # Try environment variable first
    secret = os.environ.get("JWT_SECRET")
    if secret:
        return secret

    # Try to fetch from Secrets Manager (for deployed environments)
    try:
        import boto3
        secrets = boto3.client("secretsmanager")
        # Get secret ARN from stack outputs
        outputs = load_rajee_outputs()
        secret_arn = outputs.get("JwtSecretArn")
        if secret_arn:
            response = secrets.get_secret_value(SecretId=secret_arn)
            return response["SecretString"]
    except Exception as e:
        logger.debug(f"Could not fetch JWT secret from Secrets Manager: {e}")

    # Fall back to test default (for local testing)
    return "test-secret-key-for-local-testing"
```

**File to modify:** [infra/raja_poc/stacks/rajee_envoy_stack.py](../../infra/raja_poc/stacks/rajee_envoy_stack.py)

Add CloudFormation output for JWT secret ARN:

```python
# Add after test bucket output
CfnOutput(
    self,
    "JwtSecretArn",
    value=jwt_signing_secret.secret_arn if jwt_signing_secret else "N/A",
    description="ARN of the JWT signing secret (for integration tests)",
    condition=create_test_bucket_condition if defined else None,
)
```

### Step 6: Add JWKS Endpoint Configuration

Ensure RAJEE Envoy can reach the JWKS endpoint from the control plane.

**File to modify:** [infra/raja_poc/stacks/rajee_envoy_stack.py](../../infra/raja_poc/stacks/rajee_envoy_stack.py)

Add parameters for JWKS endpoint (if not already present):

```python
def __init__(
    self,
    scope: Construct,
    construct_id: str,
    *,
    jwks_endpoint: str | None = None,
    raja_issuer: str | None = None,
    certificate_arn: str | None = None,
    **kwargs: object,
) -> None:
```

Pass to container environment:

```python
envoy_container = task_definition.add_container(
    "EnvoyProxy",
    environment={
        "ENVOY_LOG_LEVEL": "info",
        "AUTH_DISABLED": auth_disabled.value_as_string,
        "JWKS_ENDPOINT": jwks_endpoint or "",
        "RAJA_ISSUER": raja_issuer or "",
    },
)
```

**File to modify:** [infra/raja_poc/app.py](../../infra/raja_poc/app.py)

Wire up the JWKS endpoint:

```python
# After services_stack creation
rajee_envoy_stack = RajeeEnvoyStack(
    app,
    "RajeeEnvoyStack",
    jwks_endpoint=services_stack.api_url + "/.well-known/jwks.json",
    raja_issuer=f"https://{services_stack.api_url}",
    env=env,
)
```

## Testing Strategy

### Phase 1: Development Testing (Local)

```bash
# 1. Build Docker image with auth enabled
docker build -t rajee-envoy -f infra/raja_poc/assets/envoy/Dockerfile .

# 2. Run with auth enabled
docker run -d --name rajee-test \
  -e AUTH_DISABLED=false \
  -e JWKS_ENDPOINT="http://host.docker.internal:8000/.well-known/jwks.json" \
  -e RAJA_ISSUER="https://test.local" \
  -p 10000:10000 \
  rajee-envoy

# 3. Generate test token
python tests/local/generate_test_token.py \
  --bucket raja-poc-test \
  --prefix rajee-integration/

# 4. Test authorized request
curl -H "Authorization: Bearer $TOKEN" \
  -X PUT http://localhost:10000/raja-poc-test/rajee-integration/test.txt \
  -d "Hello, world"

# 5. Test unauthorized request (should get 403)
curl -H "Authorization: Bearer $TOKEN" \
  -X PUT http://localhost:10000/raja-poc-test/unauthorized/test.txt \
  -d "Should be denied"
```

### Phase 2: Integration Testing (AWS)

```bash
# 1. Deploy updated stack
cd infra && npx cdk deploy RajeeEnvoyStack

# 2. Run negative test (should fail with AUTH_DISABLED=true)
pytest tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_auth_denies_unauthorized_prefix

# 3. Enable auth in CloudFormation parameter
aws cloudformation update-stack \
  --stack-name RajeeEnvoyStack \
  --use-previous-template \
  --parameters ParameterKey=AUTH_DISABLED,ParameterValue=false

# 4. Re-run negative test (should pass now)
pytest tests/integration/test_rajee_envoy_bucket.py::test_rajee_envoy_auth_denies_unauthorized_prefix

# 5. Run all integration tests
pytest tests/integration/test_rajee_envoy_bucket.py -v
```

### Phase 3: Deployment Validation

After deploying with `AUTH_DISABLED=false` by default:

```bash
# All tests should pass
pytest tests/integration/test_rajee_envoy_bucket.py -v

# Verify CloudWatch logs show JWT validation
aws logs tail /aws/ecs/rajee-envoy --follow --filter-pattern "jwt_authn"

# Verify authorization decisions
aws logs tail /aws/ecs/rajee-envoy --follow --filter-pattern "ALLOW\|DENY"
```

## Verification Checklist

- [ ] Cedar policy created for test prefix grants
- [ ] Negative authorization test added (fails with auth disabled)
- [ ] All existing tests updated to use JWT tokens
- [ ] AUTH_DISABLED defaults changed to `false` in all environments
- [ ] JWKS endpoint configuration wired up
- [ ] Local Docker testing passes with auth enabled
- [ ] Negative test passes when deployed with auth enabled
- [ ] All integration tests pass with auth enabled
- [ ] CloudWatch logs show JWT validation occurring
- [ ] CloudWatch logs show authorization decisions (ALLOW/DENY)

## Success Criteria

1. **Negative test fails** when `AUTH_DISABLED=true` (validates test works)
2. **Negative test passes** when `AUTH_DISABLED=false` (validates auth works)
3. **All existing tests pass** with auth enabled (validates token issuance works)
4. **CloudWatch logs show JWT validation** (validates filters are active)
5. **No 401/403 errors** for authorized requests (validates policy is correct)

## Rollback Plan

If issues occur after enabling auth:

```bash
# Quick rollback: Update CloudFormation parameter
aws cloudformation update-stack \
  --stack-name RajeeEnvoyStack \
  --use-previous-template \
  --parameters ParameterKey=AUTH_DISABLED,ParameterValue=true

# Or: Redeploy previous Docker image
docker build -t rajee-envoy --build-arg AUTH_DISABLED=true .
docker push <ecr-repo>/rajee-envoy:rollback
```

## Critical Files

1. ✨ **policies/policies/rajee-test-policy.cedar** - NEW: Always-allow policy for test prefix
2. ✏️ **tests/integration/helpers.py** - Add `issue_rajee_token()` helper
3. ✏️ **tests/integration/test_rajee_envoy_bucket.py** - Add negative test, update existing tests
4. ✏️ **infra/raja_poc/assets/envoy/Dockerfile** - Change default to `AUTH_DISABLED=false`
5. ✏️ **infra/raja_poc/assets/envoy/entrypoint.sh** - Change fallback to `false`
6. ✏️ **infra/docker-compose.yml** - Change default to `AUTH_DISABLED=false`
7. ✏️ **infra/raja_poc/stacks/rajee_envoy_stack.py** - Add JWKS endpoint parameters, output secret ARN
8. ✏️ **infra/raja_poc/app.py** - Wire up JWKS endpoint

## Timeline Estimate

- **Step 1** (Policy + token helper): 1 hour
- **Step 2** (Negative test): 1 hour
- **Step 3** (Update existing tests): 2 hours
- **Step 4** (Change defaults): 0.5 hours
- **Step 5** (Environment config): 1 hour
- **Step 6** (JWKS wiring): 0.5 hours
- **Testing** (local + AWS): 2 hours

**Total: ~8 hours (1 day)**
