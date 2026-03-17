from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

from raja.server import dependencies
from raja.server.logging_config import configure_logging, get_logger
from raja.server.routers import control_plane_router, failure_tests_router, probe_router

# Configure structured logging at module level
configure_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    """Lifespan context manager for startup and shutdown events."""
    logger.info("raja_server_started", version="0.2.0", title="RAJA Control Plane")
    yield


# Create FastAPI app and include routers
app = FastAPI(title="RAJA Control Plane", version="0.2.0", lifespan=lifespan)

# Mount static assets for the admin UI.
static_dir = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Include domain-specific routers
app.include_router(control_plane_router)
app.include_router(failure_tests_router)
app.include_router(probe_router)


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
    if dependencies.os.environ.get("DATAZONE_DOMAIN_ID"):
        _check("datazone", dependencies.get_datazone_client)

    status = "ok" if all(value == "ok" for value in dependency_checks.values()) else "degraded"
    config: dict[str, str] = {}
    rajee_endpoint = dependencies.os.environ.get("RAJEE_ENDPOINT")
    if rajee_endpoint:
        config["rajee_endpoint"] = rajee_endpoint
    default_principal = dependencies.os.environ.get("RAJA_DEFAULT_PRINCIPAL", "").strip()
    if default_principal:
        config["default_principal"] = default_principal
    return {"status": status, "dependencies": dependency_checks, "config": config}
