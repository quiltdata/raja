"""Probe router for testing RAJEE endpoint connectivity and enforcement."""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel

from raja.server import audit, dependencies
from raja.server.logging_config import get_logger
from raja.token import create_token_with_package_grant

logger = get_logger(__name__)

PROBE_TTL = 60  # short-lived token minted specifically for probe tests

router = APIRouter(prefix="/probe", tags=["probe"])


class RajeeProbeRequest(BaseModel):
    """Request model for a live RAJEE enforcement probe."""

    principal: str
    usl: str
    rajee_endpoint: str


@router.post("/rajee")
def probe_rajee(
    payload: RajeeProbeRequest,
    _: None = Depends(dependencies.require_admin_auth),
    secret: str = Depends(dependencies.get_jwt_secret),
    audit_table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, Any]:
    """Mint a real short-lived TAJ and send it to a live RAJEE endpoint.

    Proves the full path: RAJA mints → RAJEE validates → S3 is reached (or denied).
    """
    token = create_token_with_package_grant(
        subject=payload.principal,
        quilt_uri=payload.usl,
        mode="read",
        ttl=PROBE_TTL,
        secret=secret,
    )

    target_url = payload.rajee_endpoint.rstrip("/") + "/"
    try:
        response = httpx.head(
            target_url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
            follow_redirects=False,
        )
        all_headers = dict(response.headers.items())
        raja_headers = {
            k: v for k, v in response.headers.items() if k.lower().startswith("x-raja-")
        }
        result: dict[str, Any] = {
            "status_code": response.status_code,
            "headers": all_headers,
            "diagnostic_headers": raja_headers,
            "rajee_reachable": True,
            "principal": payload.principal,
            "usl": payload.usl,
            "endpoint": payload.rajee_endpoint,
        }
        decision = "SUCCESS"
    except httpx.RequestError as exc:
        result = {
            "status_code": None,
            "headers": {},
            "diagnostic_headers": {},
            "rajee_reachable": False,
            "error": str(exc),
            "principal": payload.principal,
            "usl": payload.usl,
            "endpoint": payload.rajee_endpoint,
        }
        decision = "ERROR"

    try:
        audit_table.put_item(
            Item=audit.build_audit_item(
                principal=payload.principal,
                action="probe.rajee",
                resource=payload.usl,
                decision=decision,
                authorization_plane_id=None,
                request_id=str(uuid.uuid4()),
            )
        )
    except Exception as exc:
        logger.warning("audit_log_write_failed", error=str(exc))

    return result


@router.get("/rajee/health")
def probe_rajee_health(
    endpoint: str = Query(..., description="RAJEE endpoint URL to check"),
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, Any]:
    """Check whether a RAJEE endpoint is reachable and returns a valid /ready response."""
    target_url = endpoint.rstrip("/") + "/ready"
    try:
        response = httpx.get(target_url, timeout=5.0)
        return {
            "reachable": True,
            "status_code": response.status_code,
            "ready": response.status_code == 200,
        }
    except httpx.RequestError as exc:
        return {
            "reachable": False,
            "status_code": None,
            "ready": False,
            "error": str(exc),
        }
