# 1. Translation Access Grants (TAJ): Logical → Physical Mapping

Quilt package manifests can include **logical → physical key mapping**.

This enables a second data-plane capability: a **Translation Access Grant (TAJ)**.

A TAJ is still anchored to the **same immutable package identifier** (the same `quilt_uri` used by package grants). The difference is how RAJEE interprets and processes the incoming request.

## 1.1 What changes vs package-grant membership enforcement

For a normal package grant, the incoming `(bucket, key)` is treated as a **physical** S3 object, and RAJEE answers:

- `ALLOW` if `(bucket, key)` is a member of the package
- `DENY` otherwise

For a TAJ, the incoming `(bucket, key)` is treated as a **logical** S3 object reference, and RAJEE performs **translation**:

a) The incoming bucket/key is interpreted **logically** (a logical namespace), not as the physical storage location.

> Let's call this a logical S3 path: s3://registry/pkg_prefix/pgk_suffix/logical_key

b) The external authorizer (Lambda) returns the **mapped physical target** `(bucket, key)` (or a small set of targets), not just yes/no.

c) A follow-on filter (e.g., Envoy Lua) **repackages** the request so the downstream call is made against the **physical** bucket/key.

This is request termination + re-signing in disguise: the platform must treat the translated request as a *new* request, executed under platform credentials.

## 1.2 Token shape for TAJ

A TAJ can reuse the same core claims as a package grant token:

- `quilt_uri` (immutable)
- `mode` (`read` / `readwrite`)

and adds one additional mechanically-enforceable claim describing the **logical request surface**:

- `logical_bucket` and `logical_key` (or a single `logical_s3_path` string)

The TAJ MUST NOT include the mapping table. TAJEE derives mappings by resolving the immutable `quilt_uri` and consulting the manifest.

## 1.3 Enforcement pipeline (TAJ)

At a high level:

1. Validate JWT (as usual)
2. Treat incoming `(bucket, key)` as **logical**
3. Resolve `quilt_uri` → manifest (cacheable; immutable)
4. Translate logical `(bucket, key)` → physical `(bucket, key)`
5. Repackage the request (e.g., Lua filter rewrites host/path/headers)
6. Execute against S3 using platform credentials

If translation fails (unknown logical key, parse failure, missing manifest), the system fails closed: `DENY`.
