from __future__ import annotations

from urllib.parse import quote

import httpx

from raja.quilt_uri import parse_quilt_uri

from .console import Console
from .state import SessionState


def _router_path_from_usl(usl: str) -> str:
    parsed = parse_quilt_uri(usl)
    logical_path = parsed.path or ""
    return f"/{parsed.registry}/{parsed.package_name}@{parsed.hash}/{logical_path}"


def run_phase3(state: SessionState, console: Console) -> None:
    usl = state.ensure_usl()
    _ = state.ensure_taj()

    console.rule("RALE CLI - EXECUTION")
    console.print(f"RAJEE endpoint: [bold]{state.config.rajee_endpoint}[/bold]")

    try:
        health = httpx.get(
            f"{state.config.server_url}/probe/rajee/health",
            params={"endpoint": state.config.rajee_endpoint},
            headers={"Authorization": f"Bearer {state.config.admin_key}"},
            timeout=10.0,
        )
    except httpx.RequestError as exc:
        raise RuntimeError(f"RAJA server not reachable at {state.config.server_url}") from exc

    if health.status_code >= 400:
        raise RuntimeError(f"RAJEE health probe failed with status {health.status_code}")

    health_body = health.json()
    if not bool(health_body.get("reachable")):
        message = f"RAJEE not reachable at {state.config.rajee_endpoint} - run health check"
        raise RuntimeError(message)

    probe = httpx.post(
        f"{state.config.server_url}/probe/rajee",
        headers={"Authorization": f"Bearer {state.config.admin_key}"},
        json={
            "principal": state.config.principal,
            "usl": usl,
            "rajee_endpoint": state.config.rajee_endpoint,
        },
        timeout=15.0,
    )
    if probe.status_code >= 400:
        raise RuntimeError(f"RAJEE probe failed with status {probe.status_code}: {probe.text}")

    probe_body = probe.json()
    headers = probe_body.get("diagnostic_headers", {})
    if isinstance(headers, dict):
        for key, value in sorted(headers.items()):
            console.print(f"{key}: {value}")

    encoded_path = quote(_router_path_from_usl(usl), safe="/@")
    router_url = state.config.rale_router_url
    object_url = f"{router_url.rstrip('/')}{encoded_path}"
    try:
        response = httpx.get(
            object_url,
            headers={"x-rale-taj": state.ensure_taj()},
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise RuntimeError(f"RALE router not reachable at {router_url}") from exc

    if response.status_code >= 400:
        message = f"Object retrieval failed with status {response.status_code}: {response.text}"
        raise RuntimeError(message)

    content = response.content
    preview = content[:400].decode("utf-8", errors="replace")
    console.print(f"Bytes received: {len(content)}")
    console.print(preview)
