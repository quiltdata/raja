from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator

from raja.server.logging_config import configure_logging, get_logger
from raja.server.routers import control_plane_router, harness_router

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


# Create FastAPI app and include routers
app = FastAPI(title="RAJA Control Plane", version="0.2.0")

# Include domain-specific routers
app.include_router(control_plane_router)
app.include_router(harness_router)


@app.on_event("startup")
def log_startup() -> None:
    """Log application startup."""
    logger.info("raja_server_started", version="0.2.0", title="RAJA Control Plane")


@app.get("/", response_class=HTMLResponse)
def admin_home() -> HTMLResponse:
    """Admin UI home page."""
    template_path = Path(__file__).parent / "templates" / "admin.html"
    html = template_path.read_text(encoding="utf-8")
    return HTMLResponse(content=html, headers={"Cache-Control": "no-store"})


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    logger.debug("health_check_requested")
    return {"status": "ok"}


@app.get("/audit")
def audit_log() -> dict[str, Any]:
    """Audit log endpoint (placeholder)."""
    return {"entries": [], "message": "Audit log not configured"}
