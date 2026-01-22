# RAJEE, Immutable Manifests, and Authorization

## 1. Purpose of this document

This document explains **how RAJEE uses immutable Quilt manifests to enforce authorization**, and what that design implies for:

- the shape of **Cedar policies** stored in Amazon Verified Permissions (AVP)
- the shape of **RAJA authorization requests**
- the mental model admins should use when granting access

This is an **admin- and architecture-facing** document, not an API reference.

---

## 2. The core idea (in one paragraph)

In Quilt, **authority is not defined by paths or file lists**.  
Authority is defined by **immutable manifests**.

Cedar determines *whether* a role may access a given manifest.  
RAJA turns that decision into a **RAJ capability token**.  
RAJEE enforces that capability by **unfolding the manifest** and allowing access *only* to the objects named by it.

The manifest does **not grant authority**.  
It **defines the boundary of authority**.

---

## 3. Why immutable manifests matter

Immutable manifests give Quilt a property most authorization systems lack:

> A stable, content-defined identifier whose meaning never changes.

This allows authorization to be expressed as:

> “Role R may read Manifest M”

without ever enumerating the files inside M in policy.

Because manifests are immutable:

- the authorized set cannot silently expand
- caching is safe
- tokens remain truthful for their lifetime
- enforcement does not rely on mutable storage structure (prefixes, folders)

---

## 4. Two kinds of grants in Quilt

Quilt supports **two distinct grant types**, because they solve different problems.

### 4.1 Path grants (location-based)

Path grants authorize access to:

- a bucket
- an optional path (prefix or exact key)

They are useful for:

- infrastructure data
- shared prefixes
- operational workflows

Path grants compile into Cedar policies over **S3Path resources** and explicit S3 actions.

### 4.2 Manifest grants (content-based)

Manifest grants authorize access to:

- an **immutable manifest identifier** (as Quilt package with hash)

They are useful for:

- packages
- datasets
- any collection with thousands of files
- cross-bucket layouts

Manifest grants compile into **one Cedar policy per grant**, regardless of file count.

This document focuses on **manifest grants**.

---

## 5. Cedar model for manifest grants

### 5.1 Resource model

Cedar treats each immutable manifest as a first-class resource:

- Resource type: `Manifest`
- Resource ID: the immutable manifest identifier (hash or versioned ID)

Cedar does **not** know or care about the files inside the manifest.

### 5.2 Action model

Cedar actions are **package-level**, not S3-level:

- `quilt:ReadPackage`
- `quilt:WritePackage`

This avoids leaking storage details into policy.

### 5.3 Example Cedar policy

```cedar
permit(
  principal == Role::"analyst",
  action == Action::"quilt:ReadPackage",
  resource == Manifest::"pkg-abc@sha256:deadbeef"
);
```

This policy says exactly one thing:

> The role `analyst` may read the package identified by this immutable manifest.

It does **not** enumerate buckets, paths, or files.

---

## 6. RAJA authorization requests for manifest grants

### 6.1 What RAJA asks Cedar

When a client requests access to a package, RAJA asks AVP:

- principal: the role
- action: `quilt:ReadPackage` or `quilt:WritePackage`
- resource: the manifest ID
- optional context: time, client posture, etc.

Cedar returns **ALLOW or DENY**.

### 6.2 What goes into the RAJ (JWT)

If allowed, RAJA mints a RAJ containing only **mechanically enforceable claims**:

- `package_uri` (immutable)
- `mode` (read or readwrite)
- `exp` / `nbf`
- `aud`
- optional audit metadata

The RAJ does **not** contain:

- file lists
- prefixes
- buckets
- mutable references

The RAJ is a **capability**: possession implies authority.

---

## 7. How RAJEE enforces manifest grants

RAJEE is the **enforcement point**.

For each client request:

1. RAJEE validates the RAJ:
   - signature
   - audience
   - expiry
   - action compatibility

2. RAJEE resolves the manifest:
   - using the immutable `package_uri`
   - from a trusted, canonical location
   - optionally from cache

3. RAJEE enforces membership:
   - requested `(bucket, key)` must be a member of the manifest
   - no other objects are permitted

4. RAJEE executes the S3 operation on behalf of the client.

At no point does RAJEE:

- consult Cedar again
- trust caller-supplied paths
- allow access outside the manifest boundary

---

## 8. Why this does not introduce ambient authority

This design remains **capability-based**, not ambient.

- Authority originates in Cedar.
- Authority is made explicit in the RAJ.
- The manifest is used only as **evidence** to check membership.

The client cannot:

- choose the manifest
- modify the manifest
- expand the authorized set

The manifest **constrains** authority; it does not create it.

---

## 9. Implications for admins

### 9.1 What admins grant

Admins grant access to:

- **packages (manifests)**, or
- **paths**

They do not grant access to individual files.

### 9.2 Why grants scale

A single manifest grant can safely authorize:

- thousands of files
- across multiple buckets
- without policy explosion

Revocation is simple:

- disable the grant
- let outstanding RAJs expire

---

## 10. Design invariants (non-negotiable)

- Manifests used for authorization are immutable.
- Cedar policies never enumerate files.
- RAJ scopes are explicit and minimal.
- RAJEE enforces exact membership.
- AVP is a decision engine, not a data catalog.

---

## 11. Summary

Manifest grants invert traditional authorization:

- IAM authorizes **locations**
- Quilt authorizes **meaning**

By anchoring authority to immutable manifests and enforcing it via RAJEE, Quilt achieves:

- precision
- scale
- auditability
- and security properties that prefix-based IAM cannot provide.
