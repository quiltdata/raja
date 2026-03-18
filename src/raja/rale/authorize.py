from __future__ import annotations

from urllib.parse import quote

import httpx

from raja.aws_sigv4 import build_sigv4_request
from raja.manifest import resolve_package_map
from raja.quilt_uri import parse_quilt_uri
from raja.token import decode_token

from .console import Console
from .state import SessionState


def run_authorize(state: SessionState, console: Console) -> None:
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
    authorizer_path = quote(f"/{parsed.registry}/{parsed.package_name}/{parsed.path}", safe="/")

    authorizer_url = state.config.rale_authorizer_url
    request_url = f"{authorizer_url.rstrip('/')}{authorizer_path}"
    request = build_sigv4_request(method="GET", url=request_url)
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.send(request)
    except httpx.RequestError as exc:
        raise RuntimeError(f"RALE authorizer not reachable at {authorizer_url}") from exc

    if response.status_code == 403:
        try:
            deny_body = response.json()
        except Exception:
            deny_body = {}
        detail = deny_body.get("error", "")
        if detail:
            raise RuntimeError(f"DENY - {detail}")
        raise RuntimeError("DENY - no DataZone package grant permits this principal + package")
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
    for key in ["sub", "grants", "manifest_hash", "package_name", "registry", "iat", "exp"]:
        if key in claims:
            console.print(f"  {key}: {claims[key]}")
