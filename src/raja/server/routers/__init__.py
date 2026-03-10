"""FastAPI routers for domain-specific endpoints."""

from __future__ import annotations

from raja.server.routers.control_plane import router as control_plane_router
from raja.server.routers.failure_tests import router as failure_tests_router
from raja.server.routers.probe import router as probe_router

__all__ = ["control_plane_router", "failure_tests_router", "probe_router"]
