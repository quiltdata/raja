# Issue #48: Use SageMaker

## Overview

Migrate RAJA's authorization backend from Amazon Verified Permissions (AVP) to SageMaker/DataZone as the source of truth for project-to-package grants.

## Tasks

### Define custom asset type

- Create a DataZone custom asset type (e.g., `QuiltPackage`) representing the RAJ permission unit.

### Create package assets

- Create one asset per Quilt package with metadata such as:
  - package URI (`quilt://…`)
  - backing S3 prefix
  - logical endpoint (RALE)

### Map principals

- Map each RAJ principal → SageMaker/DataZone Project (already largely done in the system).
- Existing AVP-backed admin surface (policy authoring, principal management UI/API) must be accounted for: either replaced with DataZone equivalents, wrapped, or explicitly decommissioned with no substitute for the POC.

### Adopt DataZone grants as source of truth

- Use DataZone subscription/grant state to represent:
  - Project → Package access.

### Implement grant orchestration

- Wrap the DataZone workflow behind a single internal operation such as:
  - `grant_project_access(project, package)`

### Automate control-plane workflow

- Programmatically perform:
  - asset lookup/creation
  - subscription request
  - subscription approval / grant

### Update RAJ minting logic

- **Semantic simplification (conscious POC trade-off):** AVP evaluates a full tuple of `(principal, action, resource, context)`; DataZone grants are package-level and project-scoped with no action or context dimension. This POC intentionally reduces authorization semantics to a binary `project → package` grant — fine-grained action (`read`/`write`/`admin`) and context-dependent rules are dropped.

- Replace:
  - `AVP.is_allowed(principal, action, quilt_uri, context)`
- with:
  - `DataZone.has_grant(project, package)` — action and context are ignored for now.

### Integrate grant updates

- On permission changes:
  - call DataZone APIs to update the project's grant/subscription.

### Backfill existing permissions

- Import current AVP grant state into DataZone:
  - create assets
  - create project grants

### Remove AVP dependency

- Once RAJ minting reads grants from DataZone, retire the AVP policy store.

### Optional safety phase

- Run dual evaluation (AVP + DataZone) during rollout to confirm grant parity before full cutover.
