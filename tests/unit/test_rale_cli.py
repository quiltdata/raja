from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from raja.cli import main
from raja.rale.config import ConfigOverrides, resolve_config, resolve_mode, validate_config
from raja.rale.state import ResolvedConfig


def test_resolve_mode_prefers_tty_defaults() -> None:
    assert resolve_mode(requested_auto=False, requested_manual=False, stdin_is_tty=True) == "manual"
    assert resolve_mode(requested_auto=False, requested_manual=False, stdin_is_tty=False) == "auto"


def test_resolve_mode_rejects_conflicting_flags() -> None:
    with pytest.raises(ValueError, match="Choose only one"):
        resolve_mode(requested_auto=True, requested_manual=True, stdin_is_tty=True)


def test_resolve_config_priority_env_over_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path.joinpath("cli.toml")
    config_file.write_text(
        "\n".join(
            [
                'server_url = "http://file-server"',
                'registry = "s3://file-registry"',
                'rajee_endpoint = "http://file-rajee"',
                'admin_key = "file-admin"',
                'principal = "User::file"',
            ]
        ),
        encoding="utf-8",
    )

    monkeypatch.setenv("RAJA_CLI_CONFIG", str(config_file))
    monkeypatch.setenv("RAJA_SERVER_URL", "http://env-server")
    monkeypatch.setenv("RAJA_REGISTRY", "env-registry")
    monkeypatch.setenv("RAJEE_ENDPOINT", "http://env-rajee")
    monkeypatch.setenv("RAJA_ADMIN_KEY", "env-admin")
    monkeypatch.setenv("RAJA_PRINCIPAL", "User::env")
    monkeypatch.setenv("RAJA_TF_DIR", str(tmp_path / "missing-tf-dir"))

    resolved, _ = resolve_config(ConfigOverrides(server_url="http://flag-server"))

    assert resolved.server_url == "http://flag-server"
    assert resolved.registry == "s3://env-registry"
    assert resolved.rajee_endpoint == "http://env-rajee"
    assert resolved.admin_key == "env-admin"
    assert resolved.principal == "User::env"


def test_resolve_config_falls_back_to_server_default_principal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RAJA_SERVER_URL", "http://env-server")
    monkeypatch.setenv("RAJA_REGISTRY", "env-registry")
    monkeypatch.setenv("RAJEE_ENDPOINT", "http://env-rajee")
    monkeypatch.setenv("RAJA_ADMIN_KEY", "env-admin")
    monkeypatch.setenv("RAJA_TF_DIR", str(tmp_path / "missing-tf-dir"))
    monkeypatch.delenv("RAJA_PRINCIPAL", raising=False)
    monkeypatch.delenv("RAJA_DEFAULT_PRINCIPAL", raising=False)

    class _Response:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"config": {"default_principal": "arn:aws:iam::123456789012:user/tester"}}

    monkeypatch.setattr("raja.rale.config.httpx.get", lambda *args, **kwargs: _Response())

    resolved, _ = resolve_config()

    assert resolved.principal == "arn:aws:iam::123456789012:user/tester"


def test_validate_config_reports_required_values() -> None:
    missing = ResolvedConfig(
        server_url="",
        registry="",
        rajee_endpoint="",
        admin_key="",
        principal="test-user",
        tf_dir="infra/terraform",
    )

    errors = validate_config(missing)
    assert len(errors) == 6
    error_text = " ".join(errors)
    assert "RAJA_REGISTRY" in error_text
    assert "RAJA_ADMIN_KEY" in error_text
    assert "RAJA_SERVER_URL" in error_text
    assert "RAJEE_ENDPOINT" in error_text
    assert "RALE_AUTHORIZER_URL" in error_text
    assert "RALE_ROUTER_URL" in error_text


def test_validate_config_errors_on_missing_principal() -> None:
    missing = ResolvedConfig(
        server_url="http://server",
        registry="s3://bucket",
        rajee_endpoint="http://rajee",
        admin_key="key",
        principal="",
        tf_dir="infra/terraform",
        rale_authorizer_url="http://auth",
        rale_router_url="http://router",
    )

    errors = validate_config(missing)
    assert len(errors) == 1
    assert "principal" in errors[0].lower()


def test_resolve_config_falls_back_to_sts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RAJA_SERVER_URL", "http://env-server")
    monkeypatch.setenv("RAJA_REGISTRY", "env-registry")
    monkeypatch.setenv("RAJEE_ENDPOINT", "http://env-rajee")
    monkeypatch.setenv("RAJA_ADMIN_KEY", "env-admin")
    monkeypatch.setenv("RAJA_TF_DIR", str(tmp_path / "missing-tf-dir"))
    monkeypatch.delenv("RAJA_PRINCIPAL", raising=False)
    monkeypatch.delenv("RAJA_DEFAULT_PRINCIPAL", raising=False)

    monkeypatch.setattr("raja.rale.config.httpx.get", lambda *args, **kwargs: (_ for _ in ()).throw(Exception("no server")))
    monkeypatch.setattr(
        "raja.rale.config._load_principal_from_sts",
        lambda: "arn:aws:iam::123456789012:user/kmoore",
    )

    resolved, _ = resolve_config()

    assert resolved.principal == "arn:aws:iam::123456789012:user/kmoore"


def test_cli_check_reports_missing_required_values(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_file = tmp_path.joinpath("cli.toml")
    config_file.write_text('server_url = "http://localhost:8000"\n', encoding="utf-8")

    monkeypatch.setenv("RAJA_CLI_CONFIG", str(config_file))
    monkeypatch.delenv("RAJA_REGISTRY", raising=False)
    monkeypatch.delenv("RAJA_ADMIN_KEY", raising=False)
    monkeypatch.setenv("RAJA_TF_DIR", str(tmp_path / "missing-tf-dir"))

    result = CliRunner().invoke(main, ["check"])

    assert result.exit_code != 0
    assert "RAJA_REGISTRY" in result.output
    assert "RAJA_ADMIN_KEY" in result.output
