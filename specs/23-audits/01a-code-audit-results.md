# Code Audit Results - RAJA

Audit basis: repository source review plus direct local runs of `mypy --strict`, `ruff`, `bandit`, `pip-audit`, `uv tree --outdated`, `vulture`, and non-integration coverage.

## Findings

| severity | file | line | issue | what needs fixing |
|---|---|---:|---|---|
| HIGH | `lambda_handlers/control_plane/handler.py`, `lambda_handlers/rale_authorizer/handler.py` | `7`, `170` | The spec-mandated strict type pass does not complete. `uv run --extra dev mypy --strict src/raja lambda_handlers` stops immediately with a duplicate-module error because multiple Lambda entrypoints resolve to the same top-level module name `handler`. | The Lambda handler layout or type-check invocation needs to be made unambiguous so the full `--strict` audit can run across every handler. |
| HIGH | `src/raja/enforcer.py`, `src/raja/token.py` | coverage report | The audited coverage run (`uv run --extra test pytest --cov=src/raja --cov=lambda_handlers --cov-report=term-missing -m 'not integration'`) shows `src/raja/enforcer.py` at 69% and `src/raja/token.py` at 59%, far below the spec targets for the two most critical authorization modules. | Test coverage for the core authorization and JWT paths needs to reach the release thresholds before the audit can pass. |
| HIGH | `lambda_handlers/rale_authorizer/handler.py`, `lambda_handlers/package_resolver/handler.py` | coverage report | Lambda coverage is materially incomplete: `lambda_handlers/rale_authorizer/handler.py` is 39% covered and `lambda_handlers/package_resolver/handler.py` is 0% covered in the audited run. Error branches and whole handler surfaces remain unverified. | The Lambda handler suite needs coverage for normal and failure paths, especially for untested or effectively untested handlers. |
| MEDIUM | `lambda_handlers/rale_authorizer/handler.py`, `src/raja/manifest.py` | `152-154`, `14-16` | `bandit -r src/raja lambda_handlers -ll` reports six medium-severity findings for hard-coded `/tmp` directory usage in runtime path setup. | Temporary-directory handling in runtime initialization needs to be audited and either justified or replaced so the security static pass is clean. |
| MEDIUM | `lambda_handlers/rale_authorizer/handler.py` | `228-236` | The denied authorization response includes `manifest_hash`, `package_name`, and `registry` in the body. The audit brief explicitly requires DENY paths not to leak scope/package details. | The deny contract needs to stop exposing package-resolution details on unauthorized requests. |
| MEDIUM | `lambda_handlers/package_resolver/handler.py` | `6-18` | `vulture` reports the package resolver entrypoints as unused, and the coverage run confirms the file is entirely unexecuted. This handler surface is present in the repo but not exercised by tests or reachable quality gates. | The package resolver module needs a clear supported status: either exercised as a real entrypoint or removed/isolated from the release surface. |
| MEDIUM | `.github/workflows/ci.yml` | `12-109` | CI runs `./poe check` and unit tests, but it does not run `bandit`, `pip-audit`, `vulture`, or any coverage threshold gate tied to the audit targets. | CI enforcement needs to cover the audit checks that are currently only runnable manually. |
| LOW | `pyproject.toml` | `104`, `145-156` | The repo’s own `./poe check` task only type-checks `src` and does not cover `lambda_handlers`, even though the audit scope and spec both include all Lambda handlers. | The standard quality path needs to include the full audited source surface, not only the core library. |
| LOW | `pyproject.toml` | dependency tree | `pip-audit` found no known CVEs and `uv lock --check` passed, but `uv tree --outdated` shows multiple stale packages in production and dev tooling, including `fastapi`, `starlette`, `mangum`, `boto3`, `ruff`, and `mypy`. | Dependency freshness needs to be brought back within policy so the lockfile does not drift behind current supported releases. |

## Additional Audit Evidence

- `uv run --extra dev ruff check src lambda_handlers tests` passed.
- `uv run --extra dev ruff format --check src lambda_handlers tests` passed.
- `uv run --with pip-audit pip-audit` reported no known vulnerabilities.
- `vulture` also reported many framework-discovered entrypoints at 60% confidence; those were not counted as findings here unless corroborated by zero coverage or direct audit impact.

## Unverified / Blocked Audit Steps

- `tflint` could not be executed locally because the binary is not installed in this environment.
- The mypy run was blocked before deeper type issues in `lambda_handlers/` could be enumerated, so this report only records the verified blocker, not any hypothetical downstream errors.
