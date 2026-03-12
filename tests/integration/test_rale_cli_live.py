from __future__ import annotations

import importlib
import os

import pytest

from raja.rale.console import Console
from raja.rale.authorize import run_authorize
from raja.rale.fetch import run_fetch
from raja.rale.state import ResolvedConfig, SessionState

from .helpers import (
    parse_rale_test_quilt_uri,
    require_api_url,
    require_rajee_endpoint,
    require_rale_test_quilt_uri,
)


def _require_quilt3_runtime() -> None:
    try:
        importlib.import_module("quilt3")
        importlib.import_module("awscrt.checksums")
    except Exception:
        pytest.fail("quilt3 runtime dependencies are not fully installed (missing awscrt/quilt3)")


@pytest.mark.integration
def test_rale_authorize_mints_live_taj() -> None:
    _require_quilt3_runtime()
    admin_key = os.environ.get("RAJA_ADMIN_KEY")
    if not admin_key:
        pytest.fail("RAJA_ADMIN_KEY not set")

    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)
    usl = (
        f"quilt+s3://{parts['registry']}"
        f"#package={parts['package_name']}@{parts['hash']}&path=data.csv"
    )

    config = ResolvedConfig(
        server_url=require_api_url(),
        registry=f"s3://{parts['registry']}",
        rajee_endpoint=require_rajee_endpoint(),
        admin_key=admin_key,
        principal="test-user",
        tf_dir="infra/terraform",
    )
    state = SessionState(config=config, usl=usl)

    run_authorize(state=state, console=Console())

    assert state.taj
    assert state.taj_claims is not None
    assert state.taj_claims.get("sub") == "test-user"


@pytest.mark.integration
def test_rale_fetch_live_object() -> None:
    _require_quilt3_runtime()
    admin_key = os.environ.get("RAJA_ADMIN_KEY")
    if not admin_key:
        pytest.fail("RAJA_ADMIN_KEY not set")

    uri = require_rale_test_quilt_uri()
    parts = parse_rale_test_quilt_uri(uri)
    usl = (
        f"quilt+s3://{parts['registry']}"
        f"#package={parts['package_name']}@{parts['hash']}&path=data.csv"
    )

    config = ResolvedConfig(
        server_url=require_api_url(),
        registry=f"s3://{parts['registry']}",
        rajee_endpoint=require_rajee_endpoint(),
        admin_key=admin_key,
        principal="test-user",
        tf_dir="infra/terraform",
    )
    state = SessionState(config=config, usl=usl)

    run_authorize(state=state, console=Console())
    run_fetch(state=state, console=Console())
