# IAM Authentication for RALE Authorizer

## Problem

The RALE authorizer Lambda Function URL uses `authorization_type = "NONE"`. Any caller
can assert any `x-raja-principal` header with zero proof of identity. This makes the
entire DataZone membership check theater — the authorizer verifies the *claimed* principal
belongs to a project, but cannot verify the caller *is* that principal.

## Goal

Replace the honor-system `x-raja-principal` header with cryptographically proven IAM
identity via SigV4, then translate that IAM ARN to a DataZone membership.

---

## Changes Required

### 1. Terraform: Enable IAM auth on Lambda Function URLs

`infra/terraform/main.tf`:

- `aws_lambda_function_url.rale_authorizer`: change `authorization_type = "NONE"` → `"AWS_IAM"`
- `aws_lambda_function_url.rale_router`: same — also `"AWS_IAM"` (it receives the TAJ, but
  the caller still needs to be authenticated to prevent token replay from unauthenticated sources)
- Add `aws_lambda_permission` resources granting `lambda:InvokeFunctionUrl` to the IAM
  roles/principals that need access (owner, users, guests environment roles).

### 2. Lambda handler: Read proven identity from request context

`lambda_handlers/rale_authorizer/handler.py`, `_extract_principal`:

Current priority order:
1. `x-raja-principal` header (asserted, unproven)
2. `x-raja-jwt-payload` → `sub` (Envoy jwt_authn path)
3. `requestContext.authorizer.jwt.claims.sub`

New priority order:
1. `requestContext.authorizer.iam.userId` / `userArn` — set by Lambda when
   `authorization_type = "AWS_IAM"`; cryptographically proven by AWS
2. `x-raja-jwt-payload` → `sub` — Envoy path (jwt_authn already validated the JWT)
3. `x-raja-principal` — **remove or demote to last resort only for local/test contexts**,
   gated on an env var `RAJA_ALLOW_ASSERTED_PRINCIPAL=true`

The IAM ARN from the request context looks like:
`arn:aws:iam::123456789012:assumed-role/RoleName/session` or
`arn:aws:iam::123456789012:user/username`

Assumed-role ARNs must be normalized to the role ARN before DataZone lookup
(strip the `/session` suffix): `arn:aws:sts::ACCOUNT:assumed-role/ROLE/SESSION`
→ `arn:aws:iam::ACCOUNT:role/ROLE`.

### 3. DataZone translation: IAM ARN → DataZone user ID

This already works. `DataZoneService._get_user_id_for_principal` calls
`get_user_profile(type="IAM", userIdentifier=arn)` which resolves an IAM ARN to a
DataZone internal user ID. `_is_project_member` already uses this.

The key gap is that the *input* ARN must be the one DataZone has on record — typically
the role ARN, not the assumed-role session ARN. Normalization (step 2 above) is the fix.

### 4. CLI client: Sign requests with SigV4

`src/raja/rale/authorize.py`, `run_authorize`:

Replace plain `httpx.get(...)` with a SigV4-signed request:

- Use `botocore.auth.SigV4Auth` + `botocore.awsrequest.AWSRequest` to sign, then
  adapt to `httpx`.
- Credentials come from the ambient boto3 session (same credentials the user already
  has configured for AWS CLI / SageMaker / etc.).
- Remove the `x-raja-principal` header from the request — the Lambda will read the
  proven identity from the request context instead.
- The `state.config.principal` field becomes informational only (display/logging),
  not transmitted.

### 5. Envoy cluster config: Add SigV4 signing for RALE calls

`infra/envoy/entrypoint.sh` (the Envoy bootstrap config):

The `rale_authorizer_cluster` and `rale_router_cluster` upstream clusters need AWS
SigV4 request signing. Envoy supports this via the `aws_request_signing` HTTP filter
on the upstream cluster transport socket or as a per-route filter.

- Add `typedExtensionProtocolOptions` with `envoy.extensions.upstreams.http.v3.HttpProtocolOptions`
  and an upstream HTTP filter chain containing
  `extensions.filters.http.aws_request_signing.v3.AwsRequestSigning`.
- The filter uses the ECS task role credentials (automatically available in the container)
  to sign requests to the Lambda Function URL.
- Region and service (`lambda`) must be configured.

---

## Identity Flow After This Change

```
CLI caller
  → SigV4-signed GET (botocore credentials)
  → Lambda Function URL validates signature (AWS managed)
  → requestContext.authorizer.iam.userArn = "arn:aws:iam::ACCT:user/kevin-staging"
  → _extract_principal reads proven ARN
  → DataZoneService.find_project_for_principal(arn) → project_id
  → has_package_grant(project_id, quilt_uri) → ALLOW/DENY
  → TAJ issued to proven principal

Envoy path (unchanged in intent, updated signing)
  → ECS task role signs upstream call to Lambda Function URL
  → requestContext.authorizer.iam.userArn = ECS task role ARN
  → x-raja-principal (set by Envoy from jwt_authn-validated JWT) used as the
    *subscriber* identity for DataZone lookup
  → TAJ issued
```

Note: the Envoy path has a nuance — the *caller* of the Lambda is the ECS task role,
not the end user. The end-user identity arrives via `x-raja-principal` which Envoy sets
from the validated JWT. This is acceptable: Envoy is a trusted internal component whose
task role is controlled by us, and it has already proven the end-user JWT. We trust
Envoy to set the header honestly. The Lambda should verify the *caller* is the known
ECS task role ARN before trusting `x-raja-principal`.

---

## Open Questions

1. **Assumed-role normalization**: DataZone stores membership against role ARNs.
   SageMaker execution roles produce `arn:aws:sts::ACCT:assumed-role/ROLE/SESSION`.
   We need to confirm the exact normalization DataZone expects and test with Kevin's
   `kevin-staging` user ARN specifically.

2. **Envoy SigV4 filter availability**: Confirm the `aws_request_signing` extension
   is compiled into the Envoy build we use. If not, an alternative is a small Lambda
   proxy/sidecar that handles signing, but that adds complexity.

3. **Local/test mode**: Integration tests and local Docker runs need a way to call
   the authorizer without full SigV4. The `RAJA_ALLOW_ASSERTED_PRINCIPAL=true` escape
   hatch should only be enabled in non-production environments and gated in Terraform.
