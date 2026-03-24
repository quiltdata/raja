#!/usr/bin/env python3
"""Verify performance benchmark access and optional auth-toggle stability.

Checks:
  1. Control-plane /token returns 200 for the default seeded principal.
  2. A GET to the Envoy endpoint for the scale/1k package succeeds with the issued token.
  3. ECS execute-command can reach the Envoy admin stats port.

Optional:
  4. Exercise the benchmark's auth-disabled/auth-enabled Terraform cycle and
     assert that the same checks still succeed after auth is restored.

Usage:
  python scripts/verify_perf_access.py
  python scripts/verify_perf_access.py --exercise-auth-toggle
  python scripts/verify_perf_access.py --exercise-auth-toggle --report-path /tmp/report.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _REPO_ROOT / ".env"
_OUTPUTS_PATH = _REPO_ROOT / "infra" / "tf-outputs.json"
_SEED_STATE_PATH = _REPO_ROOT / ".rale-seed-state.json"
_TERRAFORM_DIR = _REPO_ROOT / "infra" / "terraform"
_HEALTH_PATH = "/health"
_ENV_CONTAINER_NAME = "EnvoyProxy"


@dataclass
class Context:
    api_url: str
    envoy_url: str
    ecs_cluster: str
    ecs_service: str
    admin_key: str
    principal: str
    perf_uri: str
    usl_path: str


@dataclass
class CheckResult:
    name: str
    ok: bool
    status: int | None
    detail: str


def _uri_to_usl_path(uri: str) -> str:
    """Convert quilt+s3://bucket#package=name@hash to /bucket/name@hash."""
    rest = uri.removeprefix("quilt+s3://")
    bucket, fragment = rest.split("#", 1)
    pkg_ref = fragment.removeprefix("package=")
    return f"/{bucket}/{pkg_ref}"


def _load_dotenv() -> None:
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _http(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
    retries: int = 3,
) -> tuple[int, bytes]:
    last: tuple[int, bytes] = (0, b"")
    for _ in range(retries):
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as exc:
            last = (exc.code, exc.read())
            if exc.code != 503:
                return last
            time.sleep(1)
    return last


def _decode_excerpt(body: bytes, limit: int = 200) -> str:
    return body.decode(errors="replace")[:limit]


def _text_excerpt(text: str, limit: int = 240) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return text[:limit]
    summary = lines[-1]
    return summary[:limit]


def _run_command(
    args: list[str],
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> tuple[int, str]:
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode == 0:
        output = proc.stdout.strip() or proc.stderr.strip()
    else:
        output = proc.stderr.strip() or proc.stdout.strip()
    return proc.returncode, output


def _ok(label: str) -> None:
    print(f"  \033[32m✓\033[0m  {label}")


def _fail(label: str, detail: str = "") -> None:
    msg = f"  \033[31m✗\033[0m  {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--exercise-auth-toggle",
        action="store_true",
        help=(
            "run terraform auth_disabled=true/false and verify the stack"
            " still passes after restore"
        ),
    )
    parser.add_argument(
        "--report-path",
        type=Path,
        help="write a JSON report of all phases and checks",
    )
    parser.add_argument(
        "--health-timeout",
        type=int,
        default=300,
        help="seconds to wait for ALB /health after each terraform apply (default: 300)",
    )
    return parser.parse_args()


def _load_context() -> Context:
    required_paths = [
        (_OUTPUTS_PATH, "infra/tf-outputs.json"),
        (_SEED_STATE_PATH, ".rale-seed-state.json"),
    ]
    for path, name in required_paths:
        if not path.exists():
            print(f"ERROR: {name} not found — run ./poe deploy first.", file=sys.stderr)
            raise SystemExit(1)

    outputs: dict[str, str] = json.loads(_OUTPUTS_PATH.read_text())
    seed_state: dict[str, object] = json.loads(_SEED_STATE_PATH.read_text())

    api_url = outputs.get("api_url", "").rstrip("/")
    envoy_url = outputs.get("rajee_endpoint", "").rstrip("/")
    ecs_cluster = outputs.get("ecs_cluster_name", "").strip()
    ecs_service = outputs.get("ecs_service_name", "").strip()
    admin_key = os.environ.get("RAJA_ADMIN_KEY", "")
    principal = str(seed_state.get("default_principal", ""))
    packages: dict[str, dict[str, str]] = seed_state.get("packages", {})  # type: ignore[assignment]
    perf_uri = packages.get("scale/1k", {}).get("uri", "")

    if not api_url or not envoy_url:
        print("ERROR: api_url / rajee_endpoint missing from tf-outputs.json", file=sys.stderr)
        raise SystemExit(1)
    if not admin_key:
        print("ERROR: RAJA_ADMIN_KEY not set", file=sys.stderr)
        raise SystemExit(1)
    if not principal:
        print(
            "ERROR: default_principal missing from .rale-seed-state.json"
            " — run seed_users.py",
            file=sys.stderr,
        )
        raise SystemExit(1)
    if not perf_uri:
        print(
            "ERROR: scale/1k missing from .rale-seed-state.json"
            " — run seed_packages.py",
            file=sys.stderr,
        )
        raise SystemExit(1)

    return Context(
        api_url=api_url,
        envoy_url=envoy_url,
        ecs_cluster=ecs_cluster,
        ecs_service=ecs_service,
        admin_key=admin_key,
        principal=principal,
        perf_uri=perf_uri,
        usl_path=_uri_to_usl_path(perf_uri),
    )


def _print_header(ctx: Context, *, toggle: bool) -> None:
    print("\nPerformance benchmark access check")
    print(f"  API:       {ctx.api_url}")
    print(f"  Envoy:     {ctx.envoy_url}")
    print(f"  ECS:       {ctx.ecs_cluster or '(missing)'} / {ctx.ecs_service or '(missing)'}")
    print(f"  Principal: {ctx.principal}")
    print(f"  Package:   {ctx.perf_uri}")
    print(f"  Toggle:    {'enabled' if toggle else 'disabled'}")
    print()


def _check_token(ctx: Context) -> tuple[CheckResult, str]:
    body = json.dumps({"principal": ctx.principal, "token_type": "raja", "ttl": 300}).encode()
    status, resp_bytes = _http(
        "POST",
        f"{ctx.api_url}/token",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {ctx.admin_key}",
        },
        body=body,
    )
    detail = _decode_excerpt(resp_bytes)
    if status != 200:
        return CheckResult("token", False, status, detail), ""
    token = json.loads(resp_bytes).get("token", "")
    if not token:
        return CheckResult("token", False, status, "200 response did not contain token"), ""
    return CheckResult("token", True, status, "token issued"), token


def _check_envoy_get(ctx: Context, token: str) -> CheckResult:
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    status, resp_bytes = _http("GET", f"{ctx.envoy_url}{ctx.usl_path}", headers=headers)
    detail = _decode_excerpt(resp_bytes)
    return CheckResult("envoy_get", status == 200, status, detail)


def _check_ecs_exec(ctx: Context) -> CheckResult:
    if not ctx.ecs_cluster or not ctx.ecs_service:
        return CheckResult(
            "ecs_exec",
            False,
            None,
            "ecs_cluster_name / ecs_service_name missing from tf-outputs.json",
        )
    if shutil.which("aws") is None:
        return CheckResult("ecs_exec", False, None, "aws CLI is not installed")

    code, task_output = _run_command(
        [
            "aws",
            "ecs",
            "list-tasks",
            "--cluster",
            ctx.ecs_cluster,
            "--service-name",
            ctx.ecs_service,
            "--query",
            "taskArns[0]",
            "--output",
            "text",
        ]
    )
    task_arn = task_output.strip()
    if code != 0:
        return CheckResult("ecs_exec", False, code, _text_excerpt(task_output))
    if not task_arn or task_arn == "None":
        return CheckResult("ecs_exec", False, None, "no running task returned")

    code, exec_output = _run_command(
        [
            "aws",
            "ecs",
            "execute-command",
            "--cluster",
            ctx.ecs_cluster,
            "--task",
            task_arn,
            "--container",
            _ENV_CONTAINER_NAME,
            "--interactive",
            "--command",
            "curl -s http://localhost:9901/stats",
        ]
    )
    return CheckResult("ecs_exec", code == 0, code if code else 200, _text_excerpt(exec_output))


def _run_phase(ctx: Context, label: str) -> tuple[list[CheckResult], int]:
    print(f"{label}:")
    failures = 0

    token_result, token = _check_token(ctx)
    if token_result.ok:
        _ok("/token → 200")
    else:
        _fail(f"/token → {token_result.status}", token_result.detail)
        failures += 1

    envoy_result = _check_envoy_get(ctx, token)
    if envoy_result.ok:
        _ok(f"Envoy GET {ctx.usl_path} → 200")
    else:
        _fail(f"Envoy GET {ctx.usl_path} → {envoy_result.status}", envoy_result.detail)
        failures += 1

    ecs_result = _check_ecs_exec(ctx)
    if ecs_result.ok:
        _ok("ECS execute-command → 200")
    else:
        label_text = "ECS execute-command failed"
        if ecs_result.status is None:
            label_text = "ECS execute-command probe skipped"
        _fail(label_text, ecs_result.detail)
        failures += 1

    print()
    return [token_result, envoy_result, ecs_result], failures


def _wait_for_health(ctx: Context, timeout_seconds: int) -> CheckResult:
    deadline = time.time() + timeout_seconds
    url = f"{ctx.envoy_url}{_HEALTH_PATH}"
    last_status = 0
    last_detail = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=5) as resp:
                body = resp.read()
                last_status = resp.status
                last_detail = _decode_excerpt(body)
                if resp.status == 200:
                    return CheckResult("health", True, resp.status, last_detail)
        except urllib.error.HTTPError as exc:
            last_status = exc.code
            last_detail = _decode_excerpt(exc.read())
        except Exception as exc:  # pragma: no cover - network/transient path
            last_detail = str(exc)
        time.sleep(5)
    return CheckResult(
        "health",
        False,
        last_status or None,
        last_detail or "timed out waiting for /health",
    )


def _terraform_apply(auth_disabled: bool, admin_key: str) -> CheckResult:
    if shutil.which("terraform") is None:
        return CheckResult("terraform_apply", False, None, "terraform is not installed")
    env = os.environ.copy()
    env["TF_VAR_raja_admin_key"] = admin_key
    code, output = _run_command(
        [
            "terraform",
            "apply",
            "-auto-approve",
            "-input=false",
            f"-var=auth_disabled={'true' if auth_disabled else 'false'}",
        ],
        cwd=_TERRAFORM_DIR,
        env=env,
    )
    mode = "true" if auth_disabled else "false"
    return CheckResult(
        f"terraform_apply_auth_disabled_{mode}",
        code == 0,
        code if code else 0,
        _text_excerpt(output),
    )


def _compare_phase_results(
    baseline: list[CheckResult],
    restored: list[CheckResult],
) -> list[CheckResult]:
    regressions: list[CheckResult] = []
    baseline_by_name = {result.name: result for result in baseline}
    restored_by_name = {result.name: result for result in restored}
    for name, before in baseline_by_name.items():
        after = restored_by_name[name]
        if before.ok and not after.ok:
            regressions.append(
                CheckResult(
                    f"{name}_regression",
                    False,
                    after.status,
                    f"preflight passed, post-restore failed: {after.detail}",
                )
            )
    return regressions


def _write_report(report_path: Path, report: dict[str, object]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")


def main() -> int:
    args = _parse_args()
    _load_dotenv()
    ctx = _load_context()
    _print_header(ctx, toggle=args.exercise_auth_toggle)

    total_failures = 0
    report: dict[str, object] = {
        "context": asdict(ctx),
        "phases": {},
    }

    preflight_results, failures = _run_phase(ctx, "Preflight")
    total_failures += failures
    report["phases"] = {
        "preflight": [asdict(result) for result in preflight_results],
    }

    if args.exercise_auth_toggle:
        print("Auth toggle cycle:")
        disable_result = _terraform_apply(True, ctx.admin_key)
        if disable_result.ok:
            _ok("terraform apply auth_disabled=true")
        else:
            _fail("terraform apply auth_disabled=true", disable_result.detail)
            total_failures += 1

        disable_health = _wait_for_health(ctx, args.health_timeout)
        if disable_health.ok:
            _ok("ALB /health after auth_disabled=true → 200")
        else:
            _fail(
                f"ALB /health after auth_disabled=true → {disable_health.status}",
                disable_health.detail,
            )
            total_failures += 1

        restore_result = _terraform_apply(False, ctx.admin_key)
        if restore_result.ok:
            _ok("terraform apply auth_disabled=false")
        else:
            _fail("terraform apply auth_disabled=false", restore_result.detail)
            total_failures += 1

        restore_health = _wait_for_health(ctx, args.health_timeout)
        if restore_health.ok:
            _ok("ALB /health after auth_disabled=false → 200")
        else:
            _fail(
                f"ALB /health after auth_disabled=false → {restore_health.status}",
                restore_health.detail,
            )
            total_failures += 1

        print()
        restored_results, failures = _run_phase(ctx, "Post-restore")
        total_failures += failures
        regressions = _compare_phase_results(preflight_results, restored_results)
        for result in regressions:
            _fail("Post-restore regression", result.detail)
            total_failures += 1

        report["phases"] = {
            "preflight": [asdict(result) for result in preflight_results],
            "after_disable": [asdict(disable_result), asdict(disable_health)],
            "after_restore": [
                asdict(restore_result),
                asdict(restore_health),
                *[asdict(result) for result in restored_results],
            ],
            "regressions": [asdict(result) for result in regressions],
        }

    print()
    if total_failures == 0:
        print("\033[32mAll checks passed — stack is ready for the performance benchmark.\033[0m\n")
    else:
        print(f"\033[31m{total_failures} check(s) failed.\033[0m")
        if args.exercise_auth_toggle:
            print("The auth-toggle cycle exposed at least one blocker or regression.\n")
        else:
            print("Resolve the reported blockers before running the live benchmark.\n")

    if args.report_path:
        report["summary"] = {"failures": total_failures}
        _write_report(args.report_path, report)

    return total_failures


if __name__ == "__main__":
    sys.exit(main())
