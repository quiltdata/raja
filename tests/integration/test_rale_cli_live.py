from __future__ import annotations

import importlib
import os

import pytest
from click.testing import CliRunner

from raja.cli import main
from raja.rale.authorize import run_authorize
from raja.rale.console import Console
from raja.rale.fetch import run_fetch
from raja.rale.state import ResolvedConfig, SessionState

from .helpers import (
    parse_rale_test_quilt_uri,
    require_api_url,
    require_rajee_endpoint,
    require_rale_authorizer_url,
    require_rale_router_url,
    require_rale_test_quilt_uri,
    require_test_principal,
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
        principal=require_test_principal(),
        tf_dir="infra/terraform",
        rale_authorizer_url=require_rale_authorizer_url(),
    )
    state = SessionState(config=config, usl=usl)

    run_authorize(state=state, console=Console())

    assert state.taj
    assert state.taj_claims is not None
    assert state.taj_claims.get("sub") == require_test_principal()


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
        principal=require_test_principal(),
        tf_dir="infra/terraform",
        rale_authorizer_url=require_rale_authorizer_url(),
        rale_router_url=require_rale_router_url(),
    )
    state = SessionState(config=config, usl=usl)

    run_authorize(state=state, console=Console())
    run_fetch(state=state, console=Console())


@pytest.mark.integration
def test_rale_access_cli_reports_and_verifies_live_access() -> None:
    _require_quilt3_runtime()
    admin_key = os.environ.get("RAJA_ADMIN_KEY")
    if not admin_key:
        pytest.fail("RAJA_ADMIN_KEY not set")

    result = CliRunner().invoke(
        main,
        [
            "--server-url",
            require_api_url(),
            "--registry",
            f"s3://{parse_rale_test_quilt_uri(require_rale_test_quilt_uri())['registry']}",
            "--rajee-endpoint",
            require_rajee_endpoint(),
            "--admin-key",
            admin_key,
            "access",
        ],
        env={
            **os.environ,
            "RAJA_PRINCIPAL": require_test_principal(),
            "RALE_AUTHORIZER_URL": require_rale_authorizer_url(),
            "RALE_ROUTER_URL": require_rale_router_url(),
        },
    )

    assert result.exit_code == 0, result.output
    assert "Package access" in result.output
    assert "Access verification" in result.output
    assert "OWNED" in result.output
    assert "GRANTED" in result.output
    assert "INACCESSIBLE" in result.output
    assert "DENY" in result.output
