# Quilt Cedar Authorization System

## 1. Executive summary

Quilt is adding **[Cedar](https://docs.cedarpolicy.com/)** evaluated by **[Amazon Verified Permissions](https://docs.aws.amazon.com/verified-permissions/latest/userguide/what-is-avp.html)** (AVP) as an **alternative policy engine** alongside **[AWS IAM](https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction.html)**. Cedar efficiently enables fine-grained, path-level access control within and across buckets with a simpler, more scalable policy than Quilt can support via IAM.

Admins can now easily and reliably grant a Quilt role access to specific paths (e.g., `incoming/` or `reports/2024.parquet`) rather than entire buckets. The runtime enforcement path uses an internal issuer/enforcer split:

- **RAJA**: consults AVP to decide whether access is allowed, then mints a signed authorization artifact.
- **RAJ**: the signed artifact, implemented as a **[JWT](https://datatracker.ietf.org/doc/html/rfc7519)**.
- **RAJEE**: validates the JWT mechanically and enforces S3 access through a transparent proxy, without re-judging.

Admins do **not** think in RAJ/JWT terms. Admin terminology distinguishes **Cedar** (path-level rules) from **IAM** (bucket-level policies) as alternative policy engines for different use cases.

---

## 2. Core concepts

### 2.1 Roles remain the entitlement unit

- Quilt **Roles** remain the stable unit of entitlement.
- A Role can have **IAM policies** granting bucket-level access.
- A Role can have **Cedar permissions** granting path-level access within specific buckets.
- Both policy engines can coexist; Cedar provides finer granularity where needed.

### 2.2 Buckets remain the administrative object

In the Quilt scenario, the only governed resources are **S3 buckets** (plus optional path scoping). Therefore, the natural place to configure Cedar permissions is **inside a bucket**, as a bucket-scoped ruleset.

### 2.3 "Path" semantics (single field)

Within a bucket, permissions are specified over an optional **Path** string:

- **Empty path** (`""`) means the **root prefix** and therefore the **entire bucket**.
- **Trailing slash** (e.g. `incoming/`) means a **prefix scope**.
- **No trailing slash** (e.g. `reports/2024.parquet`) means an **exact key**.

This keeps one canonical representation: **all rules are bucket + path rules**, where the empty path covers the whole bucket.

---

## 3. Authorization model

### 3.1 Admin-facing modes

The UI uses simple, stable terms:

- **Read**
- **Read / Write**

These modes are convenience bundles; Cedar encodes explicit S3 actions.

### 3.2 Action bundles (v1)

#### 3.2.1 Read bundle

- `s3:GetObject` (includes `HeadObject` operations)
- `s3:ListBucket` (scoped to the prefix via the `prefix` condition key; when path is empty, scope is the root prefix)

#### 3.2.2 Read / Write bundle

Read bundle plus:

- `s3:PutObject`
- (optionally later) multipart helpers such as `s3:AbortMultipartUpload`

**Note:** `s3:DeleteObject` is intentionally excluded from Read/Write in v1; treat delete as a separate escalation later.

### 3.3 Cedar semantics

Cedar is **order-invariant** because policies are evaluated as a set.

- `forbid(...)` dominates (total prohibition).
- `permit(...)` is considered only if no forbid applies.
- otherwise deny-by-default.

Decision rule:

1. If any applicable `forbid` matches → **DENY**
2. Else if any applicable `permit` matches → **ALLOW**
3. Else → **DENY**

---

## 4. Runtime components and request flow

### 4.1 Roles of each component

- **AVP** evaluates Cedar rules against a request (principal, action, resource, context).
- **RAJA** calls AVP and, when allowed, mints a **RAJ JWT**.
- **RAJEE** validates the RAJ JWT and enforces S3 access through a transparent Envoy proxy.
- **S3** remains the enforcement substrate; it never evaluates Cedar.

### 4.2 RAJA request shape (bucket-scoped)

Because Quilt v1 is bucket-local, a mint request is shaped as:

- `role` (principal)
- `bucket` (implicit from page context, but explicit in API)
- `path` (string, possibly empty)
- `mode` (`read` or `readwrite`)
- optional `context` (time, client posture, etc.) used only at evaluation time

RAJA expands the mode into explicit actions and asks AVP to authorize.

### 4.3 RAJ (JWT) contents

RAJ is a JWT and therefore must contain what RAJEE can validate mechanically.

#### 4.3.1 Enforceable claims

- `iss`, `aud`
- `exp`, `nbf`, `iat` (optional)
- `jti`
- `bucket`
- `path` (string; empty means root prefix)
- `actions` (explicit S3 actions)
- optional mechanical limits (bytes/requests), if needed

#### 4.3.2 Audit-only claims

- AVP decision id / trace id
- policy hashes / identifiers
- non-enforced notes

RAJEE must not branch on audit-only fields.

### 4.4 What RAJEE validates

RAJEE validates only:

- signature and key trust (issuer)
- `aud` match
- time bounds (`exp`, `nbf`)
- optional anti-replay (`jti`)
- that each requested S3 call is within `(bucket, path, actions)`

RAJEE does **not** consult AVP and does **not** interpret business intent.

### 4.5 How boto3 fits

Clients use **[boto3](https://boto3.amazonaws.com/v1/documentation/api/latest/index.html)** to access S3 through the **RAJEE transparent proxy**:

1. Client requests a JWT token from RAJA's control plane with specific grants (e.g., `s3:GetObject/my-bucket/path/`)
2. Client configures boto3 to point to the RAJEE proxy endpoint: `boto3.client('s3', endpoint_url='https://rajee.example.com')`
3. Client attaches the JWT token to S3 API requests via the `Authorization: Bearer <token>` header
4. RAJEE's Envoy proxy intercepts the request and calls its external authorizer
5. The authorizer validates the JWT and performs prefix-based authorization (pure string matching: `request.startswith(grant)`)
6. If authorized, Envoy forwards the native S3 API request to real S3 and streams the response back
7. boto3 receives the S3 response transparently

**Key characteristics:**

- **True S3 compatibility**: All boto3 operations work natively (GET, PUT, DELETE, LIST, multipart uploads, etc.)
- **Zero policy evaluation**: RAJEE performs only JWT validation + prefix matching (no AVP, no DynamoDB lookups)
- **Streaming**: No size limits (unlike Lambda-based approaches)
- **Transparent proxy**: Envoy forwards requests unmodified; S3 handles all operation complexity

---

## 5. Admin UX: Bucket "Permissions" pane

### 5.1 Admin terminology

Admins configure path-level access using **Cedar** rules. Bucket-level access continues to use **IAM** policies.

Admins choose the appropriate policy engine:

- **IAM**: Coarse-grained, bucket-level permissions (e.g., "Role X can read all of bucket Y")
- **Cedar**: Fine-grained, path-level permissions (e.g., "Role X can read bucket Y under path `incoming/`")

Admins do not manage RAJ/JWTs.

### 5.2 Bucket-local configuration

Inside a bucket, an admin creates a permission rule by selecting:

- a **Role**
- a **Path** (optional)
- an **Access** mode (`Read` or `Read / Write`)

This is deliberately analogous to existing read/write semantics, but adds prefix-level scope.

### 5.3 Rendering rules in the pane

- Empty path is rendered as **(entire bucket)**.
- A trailing `/` is treated as a prefix.
- No trailing `/` is treated as an exact key.

### 5.4 Pane HTML (mock)

The v1 mock pane is implemented as a simple bucket-scoped card (see `cedar-admin.html`) with:

- A header: “Permissions”
- An “+ Add rule” action
- A rules list showing Role, Path, and Access

This UI is intentionally v1-simple and can be wired to real data later.

---

## 6. Cedar and IAM coexistence

### 6.1 Both policy engines can operate simultaneously

A role may have:

- **IAM policies** providing bucket-level access (coarse-grained, standing permissions)
- **Cedar rules** providing path-level access (fine-grained, issued credentials)

Both are valid approaches depending on the use case.

**Warning:** If a role has broad IAM permissions (e.g., `s3:*` on a bucket), those standing permissions can bypass Cedar evaluation entirely. Cedar `forbid` rules cannot override IAM-granted access. For Cedar-governed buckets, IAM policies should be minimized or removed from application roles.

### 6.2 Choosing the right policy engine

**Use IAM when:**

- Bucket-level access is sufficient (e.g., "Role X can read/write the entire analytics bucket")
- Standing permissions are acceptable
- Access patterns don't require path-level granularity

**Use Cedar when:**

- Path-level access is required (e.g., "Role X can only write to `incoming/` but read from anywhere")
- Context-aware authorization is needed (time-based, user attributes, etc.)
- Short-lived, issued credentials are preferred over standing permissions

### 6.3 Implementation note

For Cedar-governed buckets, IAM should be restricted to infrastructure roles (e.g., RAJEE execution role) while application access flows through Cedar evaluation and issued JWTs.

---

## 7. Invariants to keep the system legible

### 7.1 One bucket per rule

In Quilt v1, each Cedar rule targets exactly one bucket (implicit by being configured on the bucket page).

### 7.2 One path string per rule

Each rule uses a single `path` string (possibly empty) to avoid key/prefix dual fields.

### 7.3 Simple modes compile to explicit actions

UI modes remain stable; the compiled action bundle is the precise policy.

### 7.4 Forbid is reserved for invariants

Use `forbid` for safety rails (e.g., “never write under protected/”), not for ordinary “no access” (which can be represented by absence of permits).

---

## 8. Links

- Cedar docs: <https://docs.cedarpolicy.com/>
- Amazon Verified Permissions: <https://docs.aws.amazon.com/verified-permissions/latest/userguide/what-is-avp.html>
- JWT spec (RFC 7519): <https://datatracker.ietf.org/doc/html/rfc7519>
- AWS IAM: <https://docs.aws.amazon.com/IAM/latest/UserGuide/introduction.html>
- Amazon S3: <https://docs.aws.amazon.com/AmazonS3/latest/userguide/Welcome.html>
- boto3: <https://boto3.amazonaws.com/v1/documentation/api/latest/index.html>
