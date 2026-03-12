from __future__ import annotations

from urllib.parse import quote

import httpx

from raja.manifest import resolve_package_map
from raja.quilt_uri import parse_quilt_uri
from raja.token import decode_token

from .console import Console
from .state import SessionState


def _principal_id(principal: str) -> str:
    value = principal.strip()
    if not value:
        return value
    if '::"' in value and value.endswith('"'):
        return value.rsplit('::"', 1)[1][:-1]
    if "::" in value:
        return value.split("::", 1)[1].strip('"')
    return value


def run_phase2(state: SessionState, console: Console) -> None:
    usl = state.ensure_usl()
    console.rule("RALE CLI - AUTHORIZATION")
    console.print(f"Principal: [bold]{state.config.principal}[/bold]")
    console.print(f"USL: [bold]{usl}[/bold]")

    package_map = resolve_package_map(usl)
    manifest_entries = sum(len(paths) for paths in package_map.entries.values())
    console.print(f"Pinned manifest entries: {manifest_entries}")

    parsed = parse_quilt_uri(usl)
    if not parsed.path:
        raise RuntimeError("USL must include a logical file path")
    authorizer_path = quote(f"/{parsed.registry}/{parsed.package_name}/{parsed.path}", safe="/@")

    try:
        response = httpx.get(
            f"{state.config.rajee_endpoint.rstrip('/')}{authorizer_path}",
            headers={"x-raja-principal": _principal_id(state.config.principal)},
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise RuntimeError(f"RAJEE not reachable at {state.config.rajee_endpoint}") from exc

    if response.status_code == 403:
        raise RuntimeError("DENY - no Cedar policy permits this principal + action + resource")
    if response.status_code >= 400:
        message = f"TAJ request failed with status {response.status_code}: {response.text}"
        raise RuntimeError(message)

    body = response.json()
    token = body.get("token")
    if not isinstance(token, str) or not token:
        raise RuntimeError("Token response missing token")

    claims = decode_token(token)
    state.taj = token
    state.taj_claims = claims

    console.print("TAJ issued and decoded claims:")
    for key in ["sub", "aud", "quilt_uri", "mode", "iat", "exp"]:
        if key in claims:
            console.print(f"  {key}: {claims[key]}")
