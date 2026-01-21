
# Cedar / AVP Authorization Model (Materialized)

## Overview

Quilt's Cedar authorization is modeled as a **three-level, materialized hierarchy**:

1. **Package Grants** – high-level admin intent
2. **Path Rules** – bucket-local operational rules
3. **AVP Policies** – compiled Cedar artifacts

Each level is **explicitly stored**, not inferred at runtime. This ensures auditability, debuggability, and deterministic publishing.

The hierarchy is:

PackageGrant → PathRule → AVPPolicy

**Related Documentation:**

- [cedar-avp.sql](cedar-avp.sql) - Complete PostgreSQL schema implementation
- [cedar-quilt.md](cedar-quilt.md) - Runtime architecture (RAJA/RAJ/RAJEE)

---

## Invariants

These invariants keep the system legible and deterministic:

- Every AVP policy belongs to exactly one Path Rule.
- Every Path Rule belongs to at most one Package Grant.
- Grants never directly generate AVP policies.
- The empty path (`""`) always means root prefix.
- AVP is never treated as the system of record (Postgres is the source of truth).

---

## 1. Package Grants (Intent Layer)

A **Package Grant** represents the human intent:

> “Role R may Read / ReadWrite Package P.”

Characteristics:

- May span **multiple buckets**
- May span **multiple prefixes or keys**
- Is the unit of revocation and audit
- Does **not** enumerate individual files (packages may contain thousands)

### Stored in Postgres

Example fields:

- `id`
- `package_id`
- `role_id`
- `mode` (`read | readwrite`)
- `enforcement` (`prefix_envelope | manifest_enforced`)
- `enabled`
- `created_by`, `created_at`

#### Enforcement Strategies

The `enforcement` field determines how package contents are translated into Path Rules:

- **`prefix_envelope`**: Grants access to entire bucket/prefix envelopes containing the package.
  - Simple and efficient
  - May grant access to files outside the package (anything under the same prefix)
  - Example: Package in `data/reports/2024/` grants access to entire `data/reports/2024/` prefix

- **`manifest_enforced`**: Grants access only to specific files listed in the package manifest.
  - Precise and restrictive
  - Requires materializing individual file paths as Path Rules
  - May generate many Path Rules for large packages
  - Example: Package with 1000 files creates up to 1000 Path Rules (or bucketed prefixes)

**Note:** RAJEE does not distinguish between enforcement strategies at runtime—it only validates JWT scopes. The enforcement strategy affects only how Package Grants are expanded into Path Rules during compilation.

### Package Grant Expansion Algorithm

When a Package Grant is created or updated:

1. Query the package manifest for all (bucket, path) pairs
2. If `enforcement = prefix_envelope`:
   - Compute minimal prefix envelopes covering all package files
   - Create one Path Rule per (bucket, prefix) envelope
3. If `enforcement = manifest_enforced`:
   - Create one Path Rule per unique file path in the manifest
   - Or optimize by creating prefix rules if files cluster naturally
4. Set `origin = derived_from_grant` and `package_grant_id` FK on each Path Rule
5. Trigger compilation for all affected Path Rules

A Package Grant never produces AVP policies directly.

---

## 2. Path Rules (Operational Layer)

A **Path Rule** is a bucket-local authorization rule:

> “Role R may Read / ReadWrite path X in bucket B.”

Path Rules are:

- Always scoped to **exactly one bucket**
- Always scoped to **exactly one path string**
  - empty string (`""`) = root prefix (entire bucket)
  - trailing `/` = prefix
  - otherwise = exact key
- Derived either:
  - manually (via Bucket Permissions pane), or
  - automatically (from a Package Grant)

### Materialization rule (important)

**All Package Grants are materialized into one or more Path Rules.**

There are no “virtual” rules.

Each derived Path Rule stores:

- `origin = derived_from_grant`
- `package_grant_id` (foreign key)

Manual rules store:

- `origin = manual`
- `package_grant_id = NULL`

### Stored in Postgres

Example fields:

- `id`
- `bucket`
- `path`
- `role_id`
- `mode`
- `origin`
- `package_grant_id` (nullable FK)
- `enabled`

---

## 3. AVP Policies (Compiled Layer)

AVP policies are **pure compilation artifacts**.

Each Path Rule expands into **multiple Cedar `permit` statements**, one per explicit action.

Example:

- Read ⇒ `s3:GetObject`, `s3:ListBucket`
- ReadWrite ⇒ Read + `s3:PutObject` (+ multipart helpers)

### Deterministic policy IDs

Each compiled policy uses a deterministic ID derived from the Path Rule:

```path
quilt:pathrule:<path_rule_id>:<action>
```

Example:

- `quilt:pathrule:8f12...:s3:GetObject`
- `quilt:pathrule:8f12...:s3:ListBucket`

This guarantees:

- safe updates (overwrite by ID)
- safe deletes (delete by ID)
- easy reconciliation

### Stored in AVP (tracked in Postgres)

Postgres tracks bindings:

- `path_rule_id`
- `policy_id` (AVP policyId)
- `action`
- `policy_hash` (SHA-256 of Cedar policy text)

#### Policy Hash Usage

The `policy_hash` field enables:

- **Drift Detection**: Compare Postgres hash with actual AVP policy to detect unauthorized changes
- **Change Detection**: Skip AVP updates if policy content hasn't changed (idempotent publishing)
- **Reconciliation**: Identify policies that need to be updated or recreated during sync operations
- **Audit Trail**: Track when policy content changed vs when metadata changed

---

## 4. Publishing Flow

### Create / Update Grant

1. Admin creates or updates a Package Grant.
2. Quilt computes the required bucket/path envelopes.
3. Corresponding Path Rules are **created or updated**.
4. Path Rules are compiled into Cedar policies.
5. AVP policies are created/updated by deterministic `policyId`.

### Delete / Disable Grant

**Soft Delete (Disable):**

1. Package Grant `enabled` flag set to `false`
2. All derived Path Rules are disabled (`enabled = false`)
3. AVP policies are deleted from AVP
4. Postgres records remain for audit trail

**Hard Delete (Cascade):**

1. Package Grant row is deleted from Postgres
2. All derived Path Rules are CASCADE deleted
3. All AVP policies bound to those Path Rules are deleted
4. No audit trail remains (use soft delete for compliance)

---

## 5. Admin UI Mapping

### Package Page: Grants

- Displays Package Grants
- Shows implied bucket/path envelopes
- Revocation removes all derived access

### Bucket Page: Permissions

- Displays **all Path Rules for the bucket**
- Includes:
  - manual rules
  - derived rules (tagged “From grant: <package_id>”)
- No distinction in enforcement semantics

---

## 6. Why Materialization Is Required

Materializing Path Rules provides:

- **Auditability**
  - “Why can role R access bucket B/path P?” → traceable to a Grant
- **Operational clarity**
  - Bucket pages show the full effective access picture
- **Determinism**
  - No runtime expansion of intent into policy
- **Safe deletes**
  - No orphaned AVP policies
- **Simple reconciliation**
  - Expected AVP policy set is enumerable

---

## 7. Examples

### Example 1: Package Grant for Multi-Bucket Package

**Scenario:** Package `analytics-2024` contains files across multiple locations

**Package Contents:**

- `s3://raw-data/incoming/2024/dataset.csv`
- `s3://raw-data/incoming/2024/metadata.json`
- `s3://processed/reports/2024/summary.parquet`

**Admin Action:** Grant `DataScience` role read access with `prefix_envelope` enforcement

**Result:**

1. **Package Grant Created:**
   - `package_id`: `analytics-2024`
   - `role_id`: `DataScience`
   - `mode`: `read`
   - `enforcement`: `prefix_envelope`

2. **Path Rules Materialized (2 rules):**
   - Rule 1: `bucket=raw-data`, `path=incoming/2024/`, `role_id=DataScience`, `mode=read`, `origin=derived_from_grant`
   - Rule 2: `bucket=processed`, `path=reports/2024/`, `role_id=DataScience`, `mode=read`, `origin=derived_from_grant`

3. **AVP Policies Compiled (4 policies):**
   - `quilt:pathrule:<rule1-id>:s3:GetObject`
   - `quilt:pathrule:<rule1-id>:s3:ListBucket`
   - `quilt:pathrule:<rule2-id>:s3:GetObject`
   - `quilt:pathrule:<rule2-id>:s3:ListBucket`

### Example 2: Manual Path Rule

**Scenario:** Admin needs to grant auditors access to audit logs directly

**Admin Action:** Via Bucket Permissions pane, grant `Auditors` role read access to `logs-bucket/audit/`

**Result:**

1. **Path Rule Created:**
   - `bucket=logs-bucket`
   - `path=audit/`
   - `role_id=Auditors`
   - `mode=read`
   - `origin=manual`
   - `package_grant_id=NULL`

2. **AVP Policies Compiled (2 policies):**
   - `quilt:pathrule:<rule-id>:s3:GetObject`
   - `quilt:pathrule:<rule-id>:s3:ListBucket`

**UI Display:** Bucket page shows "Manual rule" (not linked to any package)

### Example 3: Manifest-Enforced Package Grant

**Scenario:** Package `sensitive-pii` requires exact file-level access control

**Package Contents:**

- `s3://secure/customers/alice.json`
- `s3://secure/customers/bob.json`
- `s3://secure/reports/q1-summary.pdf`

**Admin Action:** Grant `Compliance` role read access with `manifest_enforced` enforcement

**Result:**

1. **Package Grant Created:**
   - `package_id`: `sensitive-pii`
   - `role_id`: `Compliance`
   - `mode`: `read`
   - `enforcement`: `manifest_enforced`

2. **Path Rules Materialized (3 rules for exact keys):**
   - Rule 1: `bucket=secure`, `path=customers/alice.json`, `origin=derived_from_grant`
   - Rule 2: `bucket=secure`, `path=customers/bob.json`, `origin=derived_from_grant`
   - Rule 3: `bucket=secure`, `path=reports/q1-summary.pdf`, `origin=derived_from_grant`

3. **AVP Policies Compiled (6 policies):**
   - Two policies per Path Rule (GetObject + ListBucket for each exact key)

**Note:** This approach provides exact file-level control but generates more rules than `prefix_envelope`.

---

## Summary

Quilt authorization uses **materialized intent**:

- Grants express *why* access exists.
- Path Rules express *where* access applies.
- AVP policies express *how* access is enforced.

Nothing is virtual. Nothing is inferred at runtime.
