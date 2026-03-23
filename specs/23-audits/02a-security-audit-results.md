# Security Audit Results - RAJA

Audit basis: repository source and configuration only. No live AWS account, deployed stage, or CloudTrail-backed verification was available during this pass.

## Findings

| severity | file | line | issue | recommendation |
|---|---|---:|---|---|
| HIGH | `infra/envoy/entrypoint.sh`, `infra/envoy/authorize.lua` | `166`, `186`, `261` | The Envoy authorization path treats `x-raja-jwt-payload` as the source of truth for claims, while the JWT filter can be removed entirely by the `AUTH_DISABLED` bootstrap path. The Lua layer does not independently prove that the header came from a verified JWT, so authorization depends on filter-chain correctness rather than the Lua layer itself. | JWT claims must not be accepted unless they are proven to come from the verified JWT-authn path. |
| MEDIUM | `infra/terraform/main.tf` | `1299`, `1309`, `1370` | The API Gateway control plane is exposed with `authorization = "NONE"` on both root and proxy resources, and the stage has no resource policy, throttling settings, or access logging configuration in Terraform. | API Gateway needs gateway-level access controls and operational protections defined in infrastructure. |
| MEDIUM | `infra/terraform/main.tf` | `1192`, `1290` | Both Lambda Function URLs are granted with `principal = "*"` and only constrained by `source_account`, which allows any IAM principal in the account with invoke permission to reach the URLs instead of only the trusted forwarder roles. | Function URL invocation must be limited to known principals, not the whole account. |
| MEDIUM | `infra/terraform/main.tf` | `632`, `933`, `1097`, `1104` | The IAM posture is broader than the role descriptions suggest: the DataZone owner role has `s3:*` over both buckets, the control plane can change Lambda configuration and call `secretsmanager:PutSecretValue`, and the authorizer can read DataZone with wildcard resource scope. | The IAM grants need to be narrowed to the minimum set of operations each role actually requires. |
| MEDIUM | `infra/terraform/main.tf`, `src/raja/server/routers/control_plane.py` | `898`, `728`, `908` | The JWT signing secret is created in Secrets Manager, but no AWS-managed rotation resource or schedule is defined. Rotation exists as application logic, not as a Secrets Manager rotation configuration. | Secret rotation needs to exist as an AWS-managed control, not only as ad hoc application behavior. |

## Unverified Live-AWS Checks

These items require deployed infrastructure or AWS API access and were not verified here:

- Effective IAM policy attachments on the deployed Lambda roles and DataZone roles.
- Deployed API Gateway stage settings, usage plans, and any WAF association.
- Deployed Lambda Function URL resource policies and caller principals.
- Live Secrets Manager rotation state and next rotation date.
- Any runtime log redaction or access-log format behavior in AWS services.

## Notes

- The JWT library itself pins `HS256` in the validation paths, so algorithm-allowlist bypass was not a finding in this pass.
- I did not see evidence in the repository of JWT payload or credential values being echoed in debug logs during the audited paths.
