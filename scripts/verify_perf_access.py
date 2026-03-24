#!/usr/bin/env python3
"""Verify performance benchmark access.

Checks:
  1. Direct S3 GET via the perf-direct Envoy route (no token required) — validates
     the PERF_DIRECT_BUCKET route is active and the IAM role can reach S3.
  2. Control-plane /token returns 200 for the default seeded principal.
  3. A GET to the Envoy endpoint for the scale/1k package succeeds with the issued token.
  4. ECS execute-command can reach the Envoy admin stats port.

Usage:
  python scripts/verify_perf_access.py
  python scripts/verify_perf_access.py --report-path /tmp/report.json
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
    perf_bucket: str


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
    return lines[-1][:limit]


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
        "--report-path",
        type=Path,
        help="write a JSON report of all checks",
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
    # Derive the perf bucket from the URI if not explicitly overridden.
    perf_bucket = os.environ.get("PERF_DIRECT_BUCKET", "")
    if not perf_bucket and perf_uri:
        # quilt+s3://{bucket}#package=... → bucket
        perf_bucket = perf_uri.removeprefix("quilt+s3://").split("#")[0]

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
        perf_bucket=perf_bucket,
    )


def _print_header(ctx: Context) -> None:
    print("\nPerformance benchmark access check")
    print(f"  API:          {ctx.api_url}")
    print(f"  Envoy:        {ctx.envoy_url}")
    print(f"  ECS:          {ctx.ecs_cluster or '(missing)'} / {ctx.ecs_service or '(missing)'}")
    print(f"  Principal:    {ctx.principal}")
    print(f"  Package:      {ctx.perf_uri}")
    print(f"  Perf bucket:  {ctx.perf_bucket}")
    print()


def _check_direct_access(ctx: Context) -> CheckResult:
    """GET the exact benchmark package path via /_perf/ (no token)."""
    url = f"{ctx.envoy_url}/_perf{ctx.usl_path}"
    status, resp_bytes = _http("GET", url)
    detail = _decode_excerpt(resp_bytes)
    ok = status == 200
    return CheckResult("direct_access", ok, status, detail)


def _check_token(ctx: Context) -> tuple[CheckResult, str]:
    body = json.dumps({"principal": ctx.principal, "token_type": "rajee", "ttl": 300}).encode()
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


def _write_report(report_path: Path, report: dict[str, object]) -> None:
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2) + "\n")


def main() -> int:
    args = _parse_args()
    _load_dotenv()
    ctx = _load_context()
    _print_header(ctx)

    total_failures = 0
    results: list[CheckResult] = []

    print("Checks:")

    direct_result = _check_direct_access(ctx)
    results.append(direct_result)
    if direct_result.ok:
        _ok(f"Direct GET /_perf/{ctx.perf_bucket}/ (no token) → {direct_result.status}")
    else:
        _fail(
            f"Direct GET /_perf/{ctx.perf_bucket}/ (no token) → {direct_result.status}",
            direct_result.detail,
        )
        total_failures += 1

    token_result, token = _check_token(ctx)
    results.append(token_result)
    if token_result.ok:
        _ok("/token → 200")
    else:
        _fail(f"/token → {token_result.status}", token_result.detail)
        total_failures += 1

    envoy_result = _check_envoy_get(ctx, token)
    results.append(envoy_result)
    if envoy_result.ok:
        _ok(f"Envoy GET {ctx.usl_path} (with token) → 200")
    else:
        _fail(
            f"Envoy GET {ctx.usl_path} (with token) → {envoy_result.status}",
            envoy_result.detail,
        )
        total_failures += 1

    ecs_result = _check_ecs_exec(ctx)
    results.append(ecs_result)
    if ecs_result.ok:
        _ok("ECS execute-command → 200")
    else:
        label_text = "ECS execute-command failed"
        if ecs_result.status is None:
            label_text = "ECS execute-command probe skipped"
        _fail(label_text, ecs_result.detail)
        total_failures += 1

    print()
    if total_failures == 0:
        print("\033[32mAll checks passed — stack is ready for the performance benchmark.\033[0m\n")
    else:
        print(f"\033[31m{total_failures} check(s) failed.\033[0m")
        print("Resolve the reported blockers before running the live benchmark.\n")

    if args.report_path:
        report: dict[str, object] = {
            "context": asdict(ctx),
            "checks": [asdict(r) for r in results],
            "summary": {"failures": total_failures},
        }
        _write_report(args.report_path, report)

    return total_failures


if __name__ == "__main__":
    sys.exit(main())
