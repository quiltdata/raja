# Security Audit Results - RAJA

Audit basis: repository source and configuration only. No live AWS account, deployed stage, or CloudTrail-backed verification was available during this pass.

## Architectural Risks

These are cross-component trust or design issues, not merely deploy-time hardening choices.

| severity | file | line | issue | what needs fixing |
|---|---|---:|---|---|
| HIGH | `infra/envoy/entrypoint.sh`, `infra/envoy/authorize.lua` | `186`, `207`, `261` | Lua authorization trusts the forwarded `x-raja-jwt-payload` header as authoritative claims data, but it does not itself prove that the header originated from a successful JWT verification step. That means the security property depends on a cross-layer invariant: `jwt_authn` must run first, untrusted inputs must never supply this header, and all future route/filter changes must preserve that contract. | The trust boundary between verified JWT processing and Lua authorization needs to be made explicit and enforceable as a hard system invariant. |

## Productization Concerns

These are real security posture gaps for a production deployment, but they are primarily infrastructure and hardening choices rather than flaws in the core authorization architecture.

| severity | file | line | issue | what needs fixing |
|---|---|---:|---|---|
| MEDIUM | `infra/terraform/main.tf` | `1299`, `1309`, `1370` | The API Gateway control plane is exposed with `authorization = "NONE"` on both root and proxy resources, and the stage has no resource policy, throttling settings, or access logging configuration in Terraform. | Gateway-level access controls and operational protections need to be defined if this stack is promoted beyond POC use. |
| MEDIUM | `infra/terraform/main.tf` | `1192`, `1290` | Both Lambda Function URLs are granted with `principal = "*"` and only constrained by `source_account`, which allows any IAM principal in the account with invoke permission to reach the URLs instead of only the trusted forwarder roles. | Function URL invocation needs tighter principal scoping for a productized deployment. |
| MEDIUM | `infra/terraform/main.tf` | `632`, `933`, `1097`, `1104` | The IAM posture is broader than the role descriptions suggest: the DataZone owner role has `s3:*` over both buckets, the control plane can change Lambda configuration and call `secretsmanager:PutSecretValue`, and the authorizer can read DataZone with wildcard resource scope. | IAM grants need to be reduced to the minimum operational scope expected for a hardened deployment. |
| MEDIUM | `infra/terraform/main.tf`, `src/raja/server/routers/control_plane.py` | `898`, `728`, `908` | The JWT signing secret is created in Secrets Manager, but no AWS-managed rotation resource or schedule is defined. Rotation exists as application logic, not as a Secrets Manager rotation configuration. | Secret lifecycle management needs to move from ad hoc application behavior to infrastructure-managed rotation before production use. |

## Unverified Live-AWS Checks

These items require deployed infrastructure or AWS API access and were not verified here:

- Effective IAM policy attachments on the deployed Lambda roles and DataZone roles.
- Deployed API Gateway stage settings, usage plans, and any WAF association.
- Deployed Lambda Function URL resource policies and caller principals.
- Live Secrets Manager rotation state and next rotation date.
- Any runtime log redaction or access-log format behavior in AWS services.

## Notes

- The JWT library itself pins `HS256` in the validation paths, so algorithm-allowlist bypass was not a finding in this pass.
- The presence of `AUTH_DISABLED` was not treated as a defect by itself. In this report, it matters only insofar as it clarifies that the Lua layer does not independently establish provenance for forwarded claims.
- I did not see evidence in the repository of JWT payload or credential values being echoed in debug logs during the audited paths.
