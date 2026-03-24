#!/usr/bin/env python3
"""Verify end-to-end access to the performance benchmark packages.

Checks:
  1. Control-plane /token returns 200 for the default seeded principal.
  2. A GET to the Envoy endpoint for the scale/1k package succeeds with the issued token.
  3. ECS execute-command can reach the Envoy admin stats port.

Both the principal and the package URI are read from .rale-seed-state.json,
which is populated by seed_users.py and seed_packages.py during ./poe deploy.

Usage:
  python scripts/verify_perf_access.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_ENV_PATH = _REPO_ROOT / ".env"
_OUTPUTS_PATH = _REPO_ROOT / "infra" / "tf-outputs.json"
_SEED_STATE_PATH = _REPO_ROOT / ".rale-seed-state.json"

# USL path from a quilt+s3://bucket#package=author/name@hash URI
def _uri_to_usl_path(uri: str) -> str:
    """Convert quilt+s3://bucket#package=name@hash to /bucket/name@hash."""
    # quilt+s3://data-yaml-spec-tests#package=scale/1k@40ff9e73
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
    import time
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
    if len(summary) > limit:
        return summary[:limit]
    return summary


def _run_aws_command(*args: str) -> tuple[int, str]:
    proc = subprocess.run(
        ["aws", *args],
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


def main() -> int:
    _load_dotenv()

    required_paths = [
        (_OUTPUTS_PATH, "infra/tf-outputs.json"),
        (_SEED_STATE_PATH, ".rale-seed-state.json"),
    ]
    for path, name in required_paths:
        if not path.exists():
            print(f"ERROR: {name} not found — run ./poe deploy first.", file=sys.stderr)
            return 1

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

    failures = 0

    print("\nPerformance benchmark access check")
    print(f"  API:       {api_url}")
    print(f"  Envoy:     {envoy_url}")
    print(f"  ECS:       {ecs_cluster or '(missing)'} / {ecs_service or '(missing)'}")
    print(f"  Principal: {principal}")
    print(f"  Package:   {perf_uri or '(not seeded)'}")
    print()

    if not api_url or not envoy_url:
        print("ERROR: api_url / rajee_endpoint missing from tf-outputs.json", file=sys.stderr)
        return 1
    if not admin_key:
        print("ERROR: RAJA_ADMIN_KEY not set", file=sys.stderr)
        return 1
    if not principal:
        print(
            "ERROR: default_principal missing from .rale-seed-state.json"
            " — run seed_users.py",
            file=sys.stderr,
        )
        return 1
    if not perf_uri:
        print(
            "ERROR: scale/1k missing from .rale-seed-state.json"
            " — run seed_packages.py",
            file=sys.stderr,
        )
        return 1

    # ── /token ────────────────────────────────────────────────────────────────
    body = json.dumps({"principal": principal, "token_type": "rajee", "ttl": 300}).encode()
    status, resp_bytes = _http(
        "POST", f"{api_url}/token",
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {admin_key}"},
        body=body,
    )
    if status == 200:
        token = json.loads(resp_bytes).get("token", "")
        _ok("/token → 200")
    else:
        _fail(f"/token → {status}", _decode_excerpt(resp_bytes))
        token = ""
        failures += 1

    # ── Envoy probe ───────────────────────────────────────────────────────────
    usl_path = _uri_to_usl_path(perf_uri)
    probe_headers: dict[str, str] = {}
    if token:
        probe_headers["Authorization"] = f"Bearer {token}"

    status, resp_bytes = _http("GET", f"{envoy_url}{usl_path}", headers=probe_headers)
    body_excerpt = _decode_excerpt(resp_bytes)

    if status == 200:
        _ok(f"Envoy GET {usl_path} → 200")
    else:
        _fail(f"Envoy GET {usl_path} → {status}", body_excerpt)
        failures += 1

    # ── ECS execute-command probe ────────────────────────────────────────────
    if not ecs_cluster or not ecs_service:
        _fail(
            "ECS execute-command probe skipped",
            "ecs_cluster_name / ecs_service_name missing from tf-outputs.json",
        )
        failures += 1
    elif shutil.which("aws") is None:
        _fail("ECS execute-command probe skipped", "aws CLI is not installed")
        failures += 1
    else:
        code, task_output = _run_aws_command(
            "ecs", "list-tasks",
            "--cluster", ecs_cluster,
            "--service-name", ecs_service,
            "--query", "taskArns[0]",
            "--output", "text",
        )
        task_arn = task_output.strip()
        if code != 0:
            _fail("ECS task lookup failed", _text_excerpt(task_output))
            failures += 1
        elif not task_arn or task_arn == "None":
            _fail("ECS task lookup failed", "no running task returned")
            failures += 1
        else:
            code, exec_output = _run_aws_command(
                "ecs", "execute-command",
                "--cluster", ecs_cluster,
                "--task", task_arn,
                "--container", "EnvoyProxy",
                "--interactive",
                "--command", "curl -s http://localhost:9901/stats",
            )
            if code == 0:
                _ok("ECS execute-command → 200")
            else:
                _fail("ECS execute-command failed", _text_excerpt(exec_output))
                failures += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if failures == 0:
        print("\033[32mAll checks passed — stack is ready for the performance benchmark.\033[0m\n")
    else:
        print(f"\033[31m{failures} check(s) failed.\033[0m")
        print("Resolve the reported blockers before running the live benchmark.\n")

    return failures


if __name__ == "__main__":
    sys.exit(main())
