from __future__ import annotations

import os
import time
import uuid
from datetime import datetime
from typing import Any

import boto3
import jwt
from fastapi import FastAPI, HTTPException, Request

from raja.rajee.authorizer import construct_request_string, extract_bearer_token, is_authorized
from raja.server.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = FastAPI()

_jwt_secret_cache: str | None = None
_cloudwatch_client: Any | None = None

METRICS_NAMESPACE = "RAJEE"


def get_jwt_secret() -> str:
    """Load the JWT signing secret once and cache it."""
    global _jwt_secret_cache
    if _jwt_secret_cache is not None:
        return _jwt_secret_cache

    secret = os.environ.get("JWT_SECRET")
    if not secret:
        raise RuntimeError("JWT_SECRET not set")
    _jwt_secret_cache = secret
    return secret


def get_cloudwatch_client() -> Any:
    global _cloudwatch_client
    if _cloudwatch_client is None:
        _cloudwatch_client = boto3.client("cloudwatch")
    return _cloudwatch_client


def emit_metric(metric_name: str, value: float, unit: str = "Count") -> None:
    try:
        client = get_cloudwatch_client()
        client.put_metric_data(
            Namespace=METRICS_NAMESPACE,
            MetricData=[
                {
                    "MetricName": metric_name,
                    "Value": value,
                    "Unit": unit,
                    "Timestamp": datetime.utcnow(),
                }
            ],
        )
    except Exception as exc:
        logger.warning("metric_emit_failed", metric=metric_name, error=str(exc))


def record_decision(
    decision: str,
    duration_ms: int,
    correlation_id: str | None,
    request_string: str | None = None,
) -> None:
    metric_name = "AuthorizationAllow" if decision == "ALLOW" else "AuthorizationDeny"
    emit_metric(metric_name, 1)
    emit_metric("AuthorizationLatency", duration_ms, "Milliseconds")
    logger.info(
        "authorization_decision",
        decision=decision,
        duration_ms=duration_ms,
        correlation_id=correlation_id,
        request_string=request_string,
    )


@app.middleware("http")
async def log_requests(request: Request, call_next: Any) -> Any:
    correlation_id = request.headers.get("x-correlation-id") or str(uuid.uuid4())
    request.state.correlation_id = correlation_id
    start_time = time.monotonic()
    response = await call_next(request)
    duration_ms = int((time.monotonic() - start_time) * 1000)
    logger.info(
        "request_complete",
        correlation_id=correlation_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=duration_ms,
    )
    response.headers["x-correlation-id"] = correlation_id
    return response


@app.post("/authorize")
async def authorize(request: Request) -> dict[str, Any]:
    """Envoy ext_authz handler for S3 prefix authorization."""
    start_time = time.monotonic()
    correlation_id = getattr(request.state, "correlation_id", None)
    try:
        body = await request.json()
    except Exception as exc:  # pragma: no cover - FastAPI handles bad JSON at runtime
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.warning("authz_invalid_json", error=str(exc), correlation_id=correlation_id)
        record_decision("DENY", duration_ms, correlation_id)
        return {
            "result": {
                "allowed": False,
                "status": {"code": 16, "message": "Invalid JSON body"},
            }
        }

    http_request = body.get("attributes", {}).get("request", {}).get("http", {})
    method = http_request.get("method", "")
    path = http_request.get("path", "")
    headers = http_request.get("headers", {}) or {}
    query = http_request.get("query_params", {}) or {}

    auth_header = headers.get("authorization", "")

    logger.info("authz_request", method=method, path=path, correlation_id=correlation_id)

    try:
        token = extract_bearer_token(auth_header)
        secret = get_jwt_secret()
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"verify_exp": True})

        grants = payload.get("grants", [])
        if not isinstance(grants, list):
            raise ValueError("Invalid grants claim")

        request_string = construct_request_string(method, path, query)

        if is_authorized(request_string, grants):
            duration_ms = int((time.monotonic() - start_time) * 1000)
            record_decision("ALLOW", duration_ms, correlation_id, request_string)
            return {"result": {"allowed": True}}

        duration_ms = int((time.monotonic() - start_time) * 1000)
        record_decision("DENY", duration_ms, correlation_id, request_string)
        return {
            "result": {
                "allowed": False,
                "status": {"code": 7, "message": "Request not covered by any grant"},
            }
        }

    except jwt.ExpiredSignatureError:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.warning("token_expired", correlation_id=correlation_id)
        record_decision("DENY", duration_ms, correlation_id)
        return {
            "result": {
                "allowed": False,
                "status": {"code": 16, "message": "Token expired"},
            }
        }
    except (jwt.InvalidTokenError, ValueError) as exc:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.warning("authorization_error", error=str(exc), correlation_id=correlation_id)
        record_decision("DENY", duration_ms, correlation_id)
        return {
            "result": {
                "allowed": False,
                "status": {"code": 16, "message": "Invalid token or request"},
            }
        }
    except Exception as exc:
        duration_ms = int((time.monotonic() - start_time) * 1000)
        logger.error(
            "authorizer_error",
            error=str(exc),
            exc_info=True,
            correlation_id=correlation_id,
        )
        record_decision("DENY", duration_ms, correlation_id)
        return {
            "result": {
                "allowed": False,
                "status": {"code": 13, "message": "Internal authorization error"},
            }
        }


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "service": "authorizer"}


@app.get("/ready")
def readiness() -> dict[str, str]:
    """Readiness check endpoint."""
    try:
        get_jwt_secret()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"status": "ready", "service": "authorizer"}
