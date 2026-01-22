from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, model_validator

from raja.server import audit, dependencies
from raja.server.logging_config import configure_logging, get_logger
from raja.server.routers import control_plane_router, failure_tests_router, harness_router

# Configure structured logging at module level
configure_logging()
logger = get_logger(__name__)


class TokenRequest(BaseModel):
    principal: str


class S3Resource(BaseModel):
    bucket: str = Field(min_length=1)
    key: str | None = None
    prefix: str | None = None

    @model_validator(mode="after")
    def _validate_selector(self) -> S3Resource:
        has_key = bool(self.key)
        has_prefix = bool(self.prefix)
        if has_key == has_prefix:
            raise ValueError("Provide exactly one of key or prefix")
        return self


class S3MintRequest(BaseModel):
    subject: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    action: Literal[
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts",
    ]
    bucket: str = Field(min_length=1)
    key: str | None = None
    prefix: str | None = None
    ttl: int | None = Field(default=None, ge=60)

    @model_validator(mode="after")
    def _validate_resource(self) -> S3MintRequest:
        has_key = bool(self.key)
        has_prefix = bool(self.prefix)
        if has_key == has_prefix:
            raise ValueError("Provide exactly one of key or prefix")
        return self


class S3VerifyRequest(BaseModel):
    token: str = Field(min_length=1)
    audience: str | None = None


class S3EnforceRequest(BaseModel):
    token: str = Field(min_length=1)
    audience: str | None = None
    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)
    action: Literal[
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts",
    ]


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    logger.info("raja_server_started", version="0.2.0", title="RAJA Control Plane")
    yield
    # Shutdown (if needed in the future)


# Create FastAPI app and include routers
app = FastAPI(title="RAJA Control Plane", version="0.2.0", lifespan=lifespan)

# Mount static assets for the admin UI.
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include domain-specific routers
app.include_router(control_plane_router)
app.include_router(failure_tests_router)
app.include_router(harness_router)


@app.get("/", response_class=HTMLResponse)
def admin_home() -> HTMLResponse:
    """Admin UI home page."""
    template_path = Path(__file__).parent / "templates" / "admin.html"
    html = template_path.read_text(encoding="utf-8")
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health() -> dict[str, Any]:
    """Health check endpoint."""
    logger.debug("health_check_requested")

    dependency_checks: dict[str, str] = {}

    def _check(name: str, fn: Any) -> None:
        try:
            fn()
            dependency_checks[name] = "ok"
        except Exception as exc:
            dependency_checks[name] = f"error: {exc}"

    _check("jwt_secret", dependencies.get_jwt_secret)
    _check("principal_table", dependencies.get_principal_table)
    _check("mappings_table", dependencies.get_mappings_table)
    _check("audit_table", dependencies.get_audit_table)

    status = "ok" if all(value == "ok" for value in dependency_checks.values()) else "degraded"
    return {"status": status, "dependencies": dependency_checks}


@app.get("/audit")
def audit_log(
    limit: int = Query(default=50, ge=1, le=200),
    next_token: str | None = None,
    principal: str | None = None,
    action: str | None = None,
    resource: str | None = None,
    start_time: int | None = Query(default=None, ge=0),
    end_time: int | None = Query(default=None, ge=0),
    table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, Any]:
    """Audit log endpoint."""
    try:
        entries, token = audit.query_audit_entries(
            table=table,
            limit=limit,
            next_token=next_token,
            principal=principal,
            action=action,
            resource=resource,
            start_time=start_time,
            end_time=end_time,
        )
    except Exception as exc:
        logger.warning("audit_query_failed", error=str(exc))
        raise HTTPException(status_code=400, detail="Invalid audit query") from exc
    return {"entries": entries, "next_token": token}
