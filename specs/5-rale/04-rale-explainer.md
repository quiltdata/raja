# RALE: RAJ-Authorizing Logical Endpoint — Concepts and Architecture

## The Problem

Traditional S3 authorization has a structural flaw: it conflates **what you're allowed to do** with **where the data lives**. Clients must know bucket names, regions, and prefixes. Bucket policies and IAM roles grant coarse access. Presigned URLs authorize individual objects but expire and cannot span collections. API gateways re-evaluate policy on every request.

None of these approaches can:
- Grant fine-grained access to a *logical dataset* that spans buckets or regions
- Hide physical infrastructure coordinates from clients
- Evaluate authorization exactly once and enforce it many times

RALE solves all three.

---

## Core Concepts

### RAJ — Resource Access JWT

A **RAJ** is a cryptographically signed token that represents *compiled authority*. Cedar policies are evaluated once at token issuance; the resulting permissions are encoded into the JWT as scopes. Enforcement later requires only signature verification and scope checking — no policy engine in the data path.

> "Authorization was fully decided when RAJA minted the RAJ."

**Format:** scopes encode `ResourceType:ResourceId:Action` (e.g., `Document:doc123:read`).

### RAJA — The Authority

**RAJA** (Resource Authorization JWT Authority) is the control plane. It:
- Accepts identity assertions and access requests
- Evaluates Cedar policies via **Amazon Verified Permissions (AVP)**
- Issues RAJ or TAJ tokens with compiled scopes

RAJA is the only Policy Decision Point (PDP). Once a token is issued, no other component makes policy decisions.

### RAJEE — The Enforcement Endpoint

**RAJEE** is the data-plane enforcer, implemented as an **Envoy proxy** with Lua filters. It:
- Validates RAJ/TAJ signatures
- Performs logical-to-physical translation
- Forwards authorized requests to the actual S3 bucket with AWS SigV4 signing

RAJEE cannot allow or deny — it can only prove where an already-authorized request must route. It is a *memberizer*, not a judge.

### TAJ — Translated Access JWT

A **TAJ** is a special RAJ scoped to a logical collection rather than a physical location. It carries a **manifest hash** that pins the token to a specific immutable snapshot of the dataset. Clients use TAJs to access logical paths (`my-dataset/data.csv`) without knowing which bucket, region, or prefix backs them.

### Manifest Authority

A **manifest** is a content-addressed, immutable index mapping logical keys to physical S3 locations. Binding a TAJ to a manifest hash means:
- Authorization cannot silently expand as the dataset evolves
- The authorized scope is completely explicit and inspectable
- Caching is safe — the manifest at a given hash never changes

This is "manifest authority": authority made concrete rather than inferred from ambient context.

### USLs — Uniform Storage Locators

**USLs** are topology-independent logical identifiers for datasets:

```
registry/package@hash/data.csv
```

They reveal nothing about buckets, regions, or vendors. Like URLs that don't leak server topology, USLs let clients think in terms of *what*, not *where*.

### Diwan — The Client-Side Runtime

The **Diwan** is the client-side orchestrator that makes all of this invisible to application code. It:
1. Intercepts outgoing S3 operations (e.g., boto3 `GetObject`)
2. Requests a TAJ from RAJA for the logical path and action
3. Attaches the token to the request
4. Routes the request to the correct regional RAJEE

Developers continue to use boto3 normally. If they never learn what a RAJ is, the Diwan has done its job.

---

## RALE: What It Adds

RALE extends RAJEE with two Lambda functions that handle the full authorization-to-routing lifecycle:

### Authorizer Lambda

Handles requests with a missing or expired TAJ:

1. Validates the caller's identity
2. Resolves the target package to a specific **manifest hash** (pinning)
3. Calls AVP for a policy decision
4. On approval, mints a TAJ containing the manifest hash
5. Caches the result by `manifest_hash:user_id`

Authorization cost is paid once per session, not per file.

### Router Lambda

Handles requests with a valid TAJ:

1. Validates the TAJ signature
2. Retrieves the pinned immutable manifest
3. Verifies the requested logical key is a member of the manifest
4. Resolves the physical S3 location (bucket, region, key)
5. Rewrites the request with AWS SigV4 and forwards it

The Router cannot make authorization decisions — it only proves membership and translates addresses.

---

## Request Flow

```
Client (boto3)
  │
  │  S3 GetObject (logical path, no TAJ)
  ▼
RALE / Authorizer Lambda
  │  validate identity
  │  pin to manifest hash
  │  call AVP → ALLOW
  │  mint TAJ
  ▼
Client receives TAJ
  │
  │  S3 GetObject (logical path, TAJ in header)
  ▼
RALE / Router Lambda
  │  verify TAJ signature
  │  retrieve pinned manifest
  │  prove logical key ∈ manifest
  │  resolve physical S3 location
  │  rewrite + SigV4 sign
  ▼
S3 (physical bucket)
  │
  └─► Object returned to client
```

Subsequent requests to the same manifest reuse the cached TAJ, hitting only the Router Lambda.

---

## Trade-offs

| Strength | Weakness |
|---|---|
| Location transparency — clients never see bucket names | Multiple Lambda invocations add latency |
| Authorization compiled once, enforced many times | Operational complexity vs. plain IAM |
| S3 API compatibility — no client changes required | Short TAJ TTLs force periodic re-auth |
| Cross-region and cross-bucket dataset support | Lambda cold starts affect tail latency |
| Immutable semantics — manifest hash prevents scope drift | |
| Full audit trail — every translation is logged | |

---

## Repo Map

| Concept | Location |
|---|---|
| Core library (models, token, enforcer, compiler) | [src/raja/](../../src/raja/) |
| Cedar policy schema and policies | [policies/](../../policies/) |
| Envoy proxy (RAJEE) config and Lua filters | [infra/raja_poc/assets/envoy/](../../infra/raja_poc/assets/envoy/) |
| Authorizer Lambda handler | [lambda_handlers/compiler/](../../lambda_handlers/compiler/) |
| Enforcer Lambda handler | [lambda_handlers/enforcer/](../../lambda_handlers/enforcer/) |
| Token service Lambda | [lambda_handlers/token_service/](../../lambda_handlers/token_service/) |
| Terraform deployment (RALE infrastructure) | [infra/terraform/](../../infra/terraform/) |
| Diwan user stories | [specs/5-rale/01-diwan-stories.md](./01-diwan-stories.md) |
| RALE Terraform implementation plan | [specs/5-rale/02-rale-terraform-impl.md](./02-rale-terraform-impl.md) |
