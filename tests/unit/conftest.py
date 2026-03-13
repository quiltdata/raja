"""Shared fixtures for unit tests."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from raja.server.routers import control_plane


@pytest.fixture(autouse=True)
def patch_datazone_domain_id() -> None:
    """Ensure DATAZONE_DOMAIN_ID is set for all unit tests that hit _policy_plane_id()."""
    with patch.object(control_plane, "DATAZONE_DOMAIN_ID", "dzd_unit_test"):
        yield
