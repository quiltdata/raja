#!/usr/bin/env python3
"""Verify end-to-end access to the performance benchmark packages.

Checks:
  1. Control-plane /token returns 200 for the default seeded principal.
  2. A GET to the Envoy endpoint for the scale/1k package returns 200.

Both the principal and the package URI are read from .rale-seed-state.json,
which is populated by seed_users.py and seed_packages.py during ./poe deploy.

Usage:
  python scripts/verify_perf_access.py
"""

from __future__ import annotations

import json
import os
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


def _http(method: str, url: str, *, headers: dict[str, str] | None = None, body: bytes | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read()


def _ok(label: str) -> None:
    print(f"  \033[32m✓\033[0m  {label}")


def _fail(label: str, detail: str = "") -> None:
    msg = f"  \033[31m✗\033[0m  {label}"
    if detail:
        msg += f"\n       {detail}"
    print(msg)


def main() -> int:
    _load_dotenv()

    for path, name in [(_OUTPUTS_PATH, "infra/tf-outputs.json"), (_SEED_STATE_PATH, ".rale-seed-state.json")]:
        if not path.exists():
            print(f"ERROR: {name} not found — run ./poe deploy first.", file=sys.stderr)
            return 1

    outputs: dict[str, str] = json.loads(_OUTPUTS_PATH.read_text())
    seed_state: dict[str, object] = json.loads(_SEED_STATE_PATH.read_text())

    api_url = outputs.get("api_url", "").rstrip("/")
    envoy_url = outputs.get("rajee_endpoint", "").rstrip("/")
    admin_key = os.environ.get("RAJA_ADMIN_KEY", "")
    principal = str(seed_state.get("default_principal", ""))
    packages: dict[str, dict[str, str]] = seed_state.get("packages", {})  # type: ignore[assignment]
    perf_uri = packages.get("scale/1k", {}).get("uri", "")

    failures = 0

    print(f"\nPerformance benchmark access check")
    print(f"  API:       {api_url}")
    print(f"  Envoy:     {envoy_url}")
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
        print("ERROR: default_principal missing from .rale-seed-state.json — run seed_users.py", file=sys.stderr)
        return 1
    if not perf_uri:
        print("ERROR: scale/1k missing from .rale-seed-state.json — run seed_packages.py", file=sys.stderr)
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
        _ok(f"/token → 200")
    else:
        _fail(f"/token → {status}", resp_bytes.decode(errors="replace")[:200])
        token = ""
        failures += 1

    # ── Envoy probe ───────────────────────────────────────────────────────────
    usl_path = _uri_to_usl_path(perf_uri)
    # x-raja-principal tells the RALE authorizer who the end-user is.
    # The ECS task role is a trusted forwarder, so this header is accepted.
    probe_headers: dict[str, str] = {"x-raja-principal": principal}
    if token:
        probe_headers["Authorization"] = f"Bearer {token}"

    status, resp_bytes = _http("GET", f"{envoy_url}{usl_path}", headers=probe_headers)
    body_excerpt = resp_bytes.decode(errors="replace")[:200]

    if status == 200:
        _ok(f"Envoy GET {usl_path} → 200")
    else:
        _fail(f"Envoy GET {usl_path} → {status}", body_excerpt)
        failures += 1

    # ── Summary ───────────────────────────────────────────────────────────────
    print()
    if failures == 0:
        print("\033[32mAll checks passed — stack is ready for the performance benchmark.\033[0m\n")
    else:
        print(f"\033[31m{failures} check(s) failed.\033[0m")
        print("Run: ./poe deploy  (re-seeds users and packages)\n")

    return failures


if __name__ == "__main__":
    sys.exit(main())
