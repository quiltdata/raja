# Diwan — Stories and Background Context

## What "Diwan" Means

Diwan operates in RAJA systems the way [historical Diwans](https://en.wikipedia.org/wiki/Diwan_(title)) managed kingdoms for Rajas — as the operational administrator that makes everything work.

---

## Background: The RAJ System

The RAJ system consists of four distinct roles:

- **RAJA** — the Authority that evaluates policy and issues credentials.
- **RAJ** — the Resource Access JWT that encodes compiled authority.
- **RAJEE** — the enforcement endpoint (typically an Envoy proxy) that validates RAJ and translates logical requests into physical ones.
- **Diwan** — the client-side runtime that acts on behalf of applications to obtain RAJ/TAJ and correctly interact with RAJEE-backed services.

Applications should never need to understand RAJA, RAJ, or RAJEE directly. Their job is to use standard clients (e.g., boto3). The Diwan exists to make this possible.

The Diwan is the administrative brain that orchestrates:

1. Discovering what resource the client intends to access.
2. Requesting the correct token from RAJA.
3. Routing the request to the correct RAJEE for the region/service.
4. Injecting the token into the request at the correct moment.
5. Allowing the client library to proceed normally.

If developers never learn what a RAJ is, the Diwan has done its job.

By default, the Diwan requests **TAJs** from RAJA rather than raw RAJs. TAJs allow developers to work entirely in a logical namespace while RAJEE performs the physical translation and enforcement.

---

## Story 1 — Logical S3 Without Physical Knowledge (TAJ Default)

**As a developer**, I want to access a logical path such as `videos/intro.mp4` using boto3,
**without knowing** which bucket, region, or prefix it lives in,
**so that** I can think in terms of logical collections rather than physical storage layout.

The Diwan should:

1. Observe the S3 operation (`GetObject`, `PutObject`, etc.) and extract the logical path.
2. Request a **TAJ** from RAJA for that logical path and action.
3. Attach the TAJ to the outgoing request.
4. Send the request to the correct regional RAJEE.
5. Let RAJEE translate logical → physical and (optionally) sign onward to AWS.

The developer continues to use boto3 normally.

## Story 1b — The Illusion of Using S3

**As a developer**, I believe I am talking directly to S3.
**In reality**, I am interacting with a logical namespace enforced by RAJEE.

The Diwan preserves this illusion by ensuring that ordinary S3 client calls are transparently transformed into RAJ-authorized requests without the developer ever learning the difference.

---

## Story 2 — Path-Based RAJ for Fine-Grained Capability

The following modes are advanced and not the default developer experience.

**As a power user**, I want to request access to a very specific logical path with narrow actions,
**so that** I can obtain a minimal capability token for a single resource.

The Diwan should:

1. Accept a request mode that explicitly asks for a path-based RAJ.
2. Call RAJA with `(logical_path, actions)`.
3. Attach the RAJ to the request to RAJEE.

This mode is explicit and opt-in. It is not the default UX.

---

## Story 3 — Package/Manifest-Based RAJ

**As a data engineer**, I want access to a collection of objects defined by a manifest,
**so that** I can work with a logical dataset that may span buckets, regions, or prefixes.

The Diwan should:

1. Accept a manifest identity (hash/revision) as the unit of authorization.
2. Request a manifest-pinned RAJ (or TAJ) from RAJA.
3. Attach that token to all requests made against the dataset.

This allows authorization by dataset rather than by path prefix.

---

## Story 4 — Region-Aware Routing

**As a platform**, I may have multiple RAJEE endpoints, one per region.

The Diwan should:

1. Maintain a mapping of region → RAJEE endpoint.
2. Select the correct RAJEE based on the client’s requested region (or default).
3. Lazily create and cache clients per region.

Developers simply request a client for a region and use it normally.

---

## Story 5 — Invisible to Client Libraries

**As an application developer**, I do not want to modify how I use boto3.

The Diwan should:

1. Hook into the client’s request lifecycle.
2. Mint tokens before requests are sent.
3. Attach tokens without altering client semantics.
4. Preserve retries, redirects, multipart flows, and normal behavior.

The Diwan must feel invisible.

---

## Story 6 — RAJA Is the Only PDP

**As a system designer**, I want policy decisions to happen only at token issuance time.

The Diwan should:

1. Never perform policy evaluation itself.
2. Always defer to RAJA for token minting.
3. Treat RAJ/TAJ as compiled authority.

RAJEE should only validate and translate, never ask for policy.

---

## Summary: The Job of the Diwan

The Diwan exists to turn ordinary client calls into correctly authorized, correctly routed RAJ interactions while hiding all RAJ system complexity from developers.
