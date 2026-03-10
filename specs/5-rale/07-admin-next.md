# Admin API: Gap Closure Spec

Three capabilities are missing from the current admin surface. This document specifies the APIs needed to close them.

---

## Gap 1: Cedar Policy Authoring

### Current state

`GET /policies` lists policies from AVP and optionally includes their Cedar statements. There are no write endpoints. Policies can only be created or modified by deploying Terraform or using the AVP console directly.

### Why it matters

Operators need to grant or revoke access to packages without a Terraform deploy cycle. The authority layer should be administrable from the admin UI without infrastructure access.

### Proposed endpoints

**Create policy**

```
POST /policies
```

Body: Cedar policy statement (string) plus optional description. RAJA validates the statement against the Cedar schema before submitting to AVP. Returns the new `policyId`.

**Get policy with full statement**

```
GET /policies/{policy_id}
```

Returns the policy metadata plus the full Cedar statement. (The existing `GET /policies?include_statements=true` is a bulk operation; this is for a single policy.)

**Update policy**

```
PUT /policies/{policy_id}
```

Body: revised Cedar statement. AVP replaces the statement atomically. Returns updated metadata.

**Delete policy**

```
DELETE /policies/{policy_id}
```

Removes the policy from the AVP policy store. This is permanent — the policy is no longer evaluated. Any principals who depended on it will receive DENY on next token issuance.

### Constraints

- All mutations must validate the Cedar statement against the schema (`policies/schema.cedar`) before submitting to AVP. An invalid statement must be rejected before it reaches AVP.
- Policy writes are logged to the audit table with `action: "policy.create"` / `policy.update"` / `"policy.delete"` and the full statement as the resource.
- The UI (see `06-live-tour.md`) should show a diff of what changed when updating a policy.

---

## Gap 2: Live RAJEE Proxy Test

### Current state

There is no live enforcement probe. The only enforcement test in the admin UI was the former `/s3-harness/enforce`, which performed a local subset check against a harness-minted token. It never touched RAJEE. This means the admin UI cannot verify that the actual Envoy filter is correctly enforcing — only that the Python library logic is correct.

### Why it matters

The harness proves the algorithm. A live RAJEE test proves the deployment. These are different claims.

### Proposed endpoint

**Live enforcement probe**

```
POST /probe/rajee
```

Body: RAJEE endpoint URL, a logical path (USL), and a principal. The server:

1. Mints a real short-lived TAJ (60s TTL) for the principal via `create_token_with_package_grant`
2. Sends a `HEAD` request to the RAJEE endpoint with the TAJ in the `Authorization` header
3. Returns RAJEE's HTTP response code, headers, and any `x-raja-*` diagnostic headers

This proves the full path: RAJA mints → RAJEE validates → S3 is reached (or denied).

**RAJEE connection check**

```
GET /probe/rajee/health?endpoint=<url>
```

Checks whether the given RAJEE endpoint is reachable and returns a valid `/ready` response. Used by the UI to verify configuration before running a live probe.

### Constraints

- The RAJEE endpoint URL is a runtime parameter, not hardcoded. In production it comes from Terraform outputs; locally it defaults to `http://localhost:10000`.
- The probe uses a short-lived token (60s TTL) minted specifically for the test, not a reused session token.
- Probe results are logged to the audit table with `action: "probe.rajee"`.

---

## Summary

| Gap | New endpoints | Depends on |
| --- | --- | --- |
| Cedar policy authoring | `POST/PUT/DELETE /policies`, `GET /policies/{id}` | AVP write permissions on the Lambda role |
| Live RAJEE probe | `POST /probe/rajee`, `GET /probe/rajee/health` | RAJEE endpoint reachable from Lambda; JWT secret for TAJ minting |

## Note: No Token Revocation

TAJ tokens are short-lived by design. Short TTLs *are* the revocation mechanism — tokens expire quickly and cannot be renewed without a new issuance decision. Adding a denylist would reintroduce stateful, per-request overhead that the architecture exists to eliminate: every enforcement call would require a DynamoDB read, coupling the data plane back to a shared mutable store.

For incident response, the correct action is **principal-level revocation**: `DELETE /principals/{principal}` immediately stops new token issuance for a compromised principal. Existing tokens expire on their own TTL. This is the zero-infrastructure path and should be the documented response procedure.

`POST /token/revoke` returns `{"status": "unsupported"}` and will remain that way.
