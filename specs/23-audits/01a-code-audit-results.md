# Code Audit Results - RAJA

Audit basis: repository source review plus direct local runs of `mypy --strict`, `ruff`, `bandit`, `pip-audit`, `uv tree --outdated`, `vulture`, and non-integration coverage.

## Resolved In This Pass

| previous severity | file | line | issue | resolution status |
|---|---|---:|---|---|
| HIGH | `lambda_handlers/control_plane/handler.py`, `lambda_handlers/rale_authorizer/handler.py`, `pyproject.toml` | `7`, `174`, `145-156` | The strict type pass was blocked by duplicate top-level Lambda module names, and the standard quality task only type-checked `src`. | Resolved. Lambda handler directories are now packages, strict `mypy` runs clean across `src/raja` and `lambda_handlers`, and `./poe check` now type-checks both surfaces. |
| MEDIUM | `lambda_handlers/rale_authorizer/handler.py` | `228-236` | The denied authorization response leaked package metadata in the body. | Resolved. The deny response no longer exposes `manifest_hash`, `package_name`, or `registry`, and unit coverage was added for this contract. |
| MEDIUM | `lambda_handlers/rale_authorizer/handler.py`, `src/raja/manifest.py` | `152-154`, `14-16` | `bandit` reported medium-severity hard-coded `/tmp` directory usage in runtime path setup. | Resolved. Runtime temp directory initialization now uses `tempfile.gettempdir()`. A fresh `bandit -r src/raja lambda_handlers -ll` run reports no medium or high findings. |
| MEDIUM | `lambda_handlers/package_resolver/handler.py` | `6-18` | The package-resolver wrapper surface was untyped, untested, and surfaced as dead code in the audit. | Partially resolved. The module now has explicit return types, export markers, and dedicated unit tests. Non-integration coverage is now 100% for this file, and the prior package-resolver-specific `vulture` finding is gone. |

## Remaining Findings

| severity | file | line | issue | current evidence |
|---|---|---:|---|---|
| HIGH | `src/raja/enforcer.py`, `src/raja/token.py` | coverage report | The refreshed audited coverage run (`uv run --extra test pytest --cov=src/raja --cov=lambda_handlers --cov-report=term-missing -m 'not integration'`) still shows `src/raja/enforcer.py` at 69% and `src/raja/token.py` at 71%, both below the audit targets for core authorization logic. |
| HIGH | `lambda_handlers/rale_authorizer/handler.py` | coverage report | Lambda handler coverage improved, but `lambda_handlers/rale_authorizer/handler.py` remains at 66% in the refreshed non-integration coverage run. Error branches and several external-call paths are still unverified. |
| MEDIUM | `.github/workflows/ci.yml` | `12-109` | CI runs `./poe check` and unit tests, but it does not run `bandit`, `pip-audit`, `vulture`, or any coverage threshold gate tied to the audit targets. | CI enforcement needs to cover the audit checks that are currently only runnable manually. |
| LOW | `pyproject.toml` | dependency tree | `pip-audit` found no known CVEs and `uv lock --check` passed, but `uv tree --outdated` shows multiple stale packages in production and dev tooling, including `fastapi`, `starlette`, `mangum`, `boto3`, `ruff`, and `mypy`. | Dependency freshness needs to be brought back within policy so the lockfile does not drift behind current supported releases. |

## Additional Audit Evidence

- `uv run --extra dev ruff check src lambda_handlers tests` passed.
- `uv run --extra dev ruff format --check src lambda_handlers tests` passed.
- `uv run --extra dev mypy --strict src/raja lambda_handlers` now passes.
- `./poe check` now passes with the Lambda handler surface included in `_typecheck`.
- `uv run --with bandit bandit -r src/raja lambda_handlers -ll` now reports no medium or high findings.
- `uv run --with pip-audit pip-audit` reported no known vulnerabilities.
- `./poe test-unit` passed: 264 unit tests green.
- The refreshed non-integration coverage run passed with 268 tests selected and produced the updated coverage figures above.
- `vulture` still reports many framework-discovered symbols at 60% confidence across the server/router surface; those remain triage noise unless corroborated by stronger evidence.

## Unverified / Blocked Audit Steps

- `tflint` could not be executed locally because the binary is not installed in this environment.
