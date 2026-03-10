# RAJ System: Administrative Concepts

A guide to what the system does, who does it, and where each responsibility lives.

---

## The Three Planes

The RAJ system separates three concerns that traditional IAM collapses into one:

| Plane | Question answered | Role |
|---|---|---|
| **Policy plane** | What is a principal allowed to do? | Cedar policies in AVP |
| **Control plane** | Who may get a token right now? | RAJA / Authorizer Lambda |
| **Data plane** | Does this request fall within compiled authority? | RAJEE / Router Lambda |

Administration lives in the **policy and control planes**. The data plane is purely mechanical — it enforces, never decides.

---

## Administrative Roles

### RAJA — The Authority

RAJA is the control plane. Its job is to evaluate a declared intent against policy and issue a signed token. It is the only system component that touches Amazon Verified Permissions (AVP).

What an admin configures in RAJA:
- Which Cedar policies govern access
- Which principals exist and what identity claims they present
- JWT signing keys (stored in Secrets Manager)
- Token TTLs

Key files:
- [lambda_handlers/control_plane/handler.py](../../lambda_handlers/control_plane/handler.py) — current token issuance endpoint
- [lambda_handlers/rale_authorizer/handler.py](../../lambda_handlers/rale_authorizer/handler.py) — RALE Authorizer Lambda
- [src/raja/token.py](../../src/raja/token.py) — JWT minting and verification
- [src/raja/compiler.py](../../src/raja/compiler.py) — Cedar policy → scope compilation
- [policies/](../../policies/) — Cedar schema and policy files

### Diwan — The Client-Side Operator

The Diwan acts on behalf of applications. From an administrative perspective, it is the agent that translates application intent into correct token requests — and routes those requests to the right RAJEE endpoint for the region.

Admins configure:
- Which RAJA endpoint to call
- Regional RAJEE endpoint mappings
- Token caching behavior

Key file:
- [specs/5-rale/01-diwan-stories.md](./01-diwan-stories.md) — full Diwan role and user stories

---

## What Gets Administered

### 1. Cedar Policies

Policies define what principals may do on which resources. They are compiled by RAJA into scope strings and stored — never evaluated at request time.

- Schema: [policies/schema.cedar](../../policies/schema.cedar)
- Policy files: [policies/policies/](../../policies/policies/)
- Compilation logic: [src/raja/compiler.py](../../src/raja/compiler.py)
- Scope model: [src/raja/scope.py](../../src/raja/scope.py)

### 2. Package Maps

A package map defines the logical-to-physical mapping for a dataset: which logical name corresponds to which S3 bucket, prefix, and region. This is the administrative configuration for location transparency.

- [src/raja/package_map.py](../../src/raja/package_map.py)
- [src/raja/manifest.py](../../src/raja/manifest.py)
- [lambda_handlers/package_resolver/handler.py](../../lambda_handlers/package_resolver/handler.py)

### 3. Token Lifecycle

Tokens (RAJ/TAJ) are short-lived. Admins control:

- **Issuance** — via RAJA/Authorizer after an AVP ALLOW decision
- **Scope** — scoped to a manifest hash (TAJ) or explicit resource list (RAJ)
- **TTL** — short TTLs are the revocation mechanism; no allowlist needed
- **Caching** — Authorizer caches by `manifest_hash:user_id` to avoid re-calling AVP on every request

Token models: [src/raja/models.py](../../src/raja/models.py)
Enforcer (scope checking): [src/raja/enforcer.py](../../src/raja/enforcer.py)

### 4. Operational Visibility

The RAJEE Envoy proxy exposes a built-in admin interface on port 9901 with live stats, cluster health, and configuration. Access is:

- **Locally:** forwarded to `localhost:9901` via docker-compose
- **In AWS:** reachable only via ECS Exec (IAM-gated) or an IP-restricted ALB listener — not public by default

Details and options: [specs/5-rale/03-envoy-ui.md](./03-envoy-ui.md)

---

## The Administrative Invariant

> No resource may decide who is legitimate.
> No sovereign may decide how a resource behaves.

In practice:
- AVP and RAJA determine legitimacy — nothing else does
- RAJEE and the Router Lambda determine routing — they cannot grant or deny
- Admins work entirely in the policy and control planes

This separation is what makes the system auditable: every access decision traces back to a single token issuance event, which traces back to a single AVP evaluation, which traces back to a specific Cedar policy.

---

## Infrastructure Map

| Responsibility | Location |
|---|---|
| AVP policy store | [infra/terraform/main.tf](../../infra/terraform/main.tf) |
| Authorizer Lambda | [lambda_handlers/rale_authorizer/](../../lambda_handlers/rale_authorizer/) |
| Router Lambda | [lambda_handlers/rale_router/](../../lambda_handlers/rale_router/) |
| Envoy proxy (RAJEE) | [infra/raja_poc/assets/envoy/](../../infra/raja_poc/assets/envoy/) |
| JWT signing key (Secrets Manager) | provisioned by Terraform, referenced in [lambda_handlers/control_plane/handler.py](../../lambda_handlers/control_plane/handler.py) |
| Core library | [src/raja/](../../src/raja/) |
