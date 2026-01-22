from __future__ import annotations

from fastapi import FastAPI

from raja.server.logging_config import configure_logging, get_logger

configure_logging()
logger = get_logger(__name__)

app = FastAPI()


@app.get("/health")
def health() -> dict[str, str]:
    """Health check endpoint."""
    return {"status": "healthy", "service": "authorizer"}


@app.get("/ready")
def readiness() -> dict[str, str]:
    """Readiness check endpoint."""
    return {"status": "ready", "service": "authorizer"}
