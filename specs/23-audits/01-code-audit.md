# Code Audit — RAJA

## Objective

Systematically assess the quality, maintainability, and correctness of the RAJA codebase
across the Python library, Lambda handlers, Lua/Envoy filter chain, and Terraform
infrastructure. Produce a prioritized remediation backlog before the next production release.

## Scope

| In | Out |
|----|-----|
| `src/raja/` core library | Third-party dependency internals |
| `lambda_handlers/` (all five handlers) | AWS managed service configuration |
| `infra/envoy/` Lua filter and Envoy config | Benchmarking / performance (see spec 03) |
| `infra/terraform/` and `infra/blueprints/` | Security threat model (see spec 02) |
| `tests/` coverage and quality | |

## Approach

### 1. Python Static Analysis

```bash
# Enforce strict typing across all source
mypy --strict src/raja/ lambda_handlers/

# Lint and format check (zero tolerance)
ruff check src/ lambda_handlers/ tests/
ruff format --check src/ lambda_handlers/ tests/

# Security-focused static analysis
pip install bandit
bandit -r src/raja/ lambda_handlers/ -ll  # flag medium+ severity
```

Target: zero mypy errors under `--strict`, zero ruff violations, zero bandit HIGH findings.

### 2. Dependency Audit

```bash
# Audit all direct and transitive deps for known CVEs
pip install pip-audit
pip-audit --requirement requirements*.txt

# Check for outdated pins in uv.lock
uv lock --check  # verify lock is consistent with pyproject.toml
uv tree --outdated  # identify stale dependencies
```

Review `infra/layers/` shared Lambda layer requirements separately — layer deps are often
pinned more conservatively than application deps and may lag behind security patches.

### 3. Lambda Handler Review

For each handler (`control_plane`, `rale_authorizer`, `rale_router`, `authorizer`,
`package_resolver`):

- **Cold start cost** — measure import time; eliminate top-level AWS client construction
  outside the handler function where possible; confirm use of Lambda Powertools lazy init.
- **Error handling** — verify all external calls (DataZone, Secrets Manager, S3) have
  explicit exception handling with structured logging; no bare `except Exception`.
- **Dead code** — run `vulture src/ lambda_handlers/` to surface unused functions and
  imports; remove or document intentional stubs.
- **Handler contracts** — confirm each handler returns correct HTTP status codes for auth
  failures (401 vs 403 vs 500) and that DENY paths never leak scope information in
  response bodies.

### 4. Lua Filter Audit (`infra/envoy/authorize.lua`, `authorize_lib.lua`)

- Trace the full JWT verification path in `authorize_lib.lua`; confirm no fallback to
  unauthenticated pass-through exists.
- Check for nil-guard gaps: any `request_handle:headers():get()` call that is not nil-checked
  before use is a potential panic path.
- Verify error responses from Lua set `x-envoy-auth-failure-mode-allow: false` equivalent
  (i.e., Envoy `failure_mode_deny: true` is set in `envoy.yaml.tmpl`).
- Confirm no debug logging that echoes JWT payloads or S3 credentials.

### 5. IaC Review

```bash
# Terraform lint
brew install tflint
tflint --init && tflint --recursive infra/terraform/

# Policy-as-code scan (CIS AWS Foundations)
pip install checkov
checkov -d infra/terraform/ --framework terraform --compact
```

Flag: unrestricted security group rules, missing encryption-at-rest settings, Lambda
functions without reserved concurrency, API Gateway stages without access logging.

### 6. Test Coverage Gap Analysis

```bash
pytest --cov=src/raja --cov=lambda_handlers \
       --cov-report=term-missing \
       --cov-report=html:coverage-report \
       -m "not integration"
```

Identify modules below 80% line coverage. Pay particular attention to:
- `src/raja/enforcer.py` — core authorization logic; must be ≥ 95%
- `src/raja/token.py` — JWT operations; must be ≥ 90%
- Error and exception branches across all Lambda handlers

## Deliverables

1. **Audit report** (`docs/audits/code-audit-results.md`) with findings table:
   `severity | file | line | issue | recommendation`
2. **GitHub issues** filed for each HIGH/MEDIUM finding with `audit` label
3. **Coverage HTML report** committed to `docs/audits/coverage/`
4. **CI enforcement** — add `bandit`, `pip-audit`, and coverage threshold gates to
   `.github/workflows/ci.yml`

## Success Criteria

| Metric | Target |
|--------|--------|
| mypy `--strict` errors | 0 |
| bandit HIGH findings | 0 |
| pip-audit known CVEs | 0 |
| Unit test line coverage (`enforcer.py`, `token.py`) | ≥ 90% |
| checkov HIGH policy violations | 0 |
| Dead code findings (vulture confidence ≥ 80%) | Triaged and documented |
