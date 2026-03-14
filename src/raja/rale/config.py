from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .state import ResolvedConfig, RunMode

load_dotenv()

DEFAULT_SERVER_URL = ""
DEFAULT_RAJEE_ENDPOINT = ""
DEFAULT_PRINCIPAL = ""
DEFAULT_TF_DIR = "infra/terraform"


@dataclass(frozen=True)
class ConfigOverrides:
    server_url: str | None = None
    registry: str | None = None
    rajee_endpoint: str | None = None
    admin_key: str | None = None
    principal: str | None = None
    tf_dir: str | None = None


def default_mode(stdin_is_tty: bool) -> RunMode:
    return "manual" if stdin_is_tty else "auto"


def resolve_mode(*, requested_auto: bool, requested_manual: bool, stdin_is_tty: bool) -> RunMode:
    if requested_auto and requested_manual:
        raise ValueError("Choose only one of --auto or --manual")
    if requested_auto:
        return "auto"
    if requested_manual:
        return "manual"
    return default_mode(stdin_is_tty)


def _read_toml_config(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        import tomllib

        raw = tomllib.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, str] = {}
    for key, value in raw.items():
        if isinstance(value, str):
            result[key] = value
    return result


def _config_path() -> Path:
    override = os.getenv("RAJA_CLI_CONFIG")
    if override:
        return Path(override).expanduser()
    return Path("~/.config/raja/cli.toml").expanduser()


def _normalize_registry(raw: str | None) -> str:
    if not raw:
        return ""
    value = raw.strip()
    if not value:
        return ""
    if "://" in value:
        return value.rstrip("/")
    return f"s3://{value.rstrip('/')}"


def _extract_tf_value(raw: Any) -> str | None:
    if isinstance(raw, dict):
        value = raw.get("value")
        if isinstance(value, str):
            return value
    if isinstance(raw, str):
        return raw
    return None


def load_terraform_outputs(tf_dir: str) -> dict[str, Any]:
    terraform = shutil.which("terraform")
    if terraform is None:
        return {}

    tf_path = Path(tf_dir)
    if not tf_path.exists() or not tf_path.is_dir():
        return {}

    try:
        proc = subprocess.run(
            [terraform, "output", "-json"],
            cwd=tf_path,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return {}

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}

    if not isinstance(parsed, dict):
        return {}
    return parsed


def resolve_config(
    overrides: ConfigOverrides | None = None,
) -> tuple[ResolvedConfig, dict[str, Any]]:
    chosen = overrides or ConfigOverrides()
    file_values = _read_toml_config(_config_path())

    tf_dir = (
        chosen.tf_dir
        or os.getenv("RAJA_TF_DIR")
        or file_values.get("RAJA_TF_DIR")
        or file_values.get("tf_dir")
        or DEFAULT_TF_DIR
    )
    tf_outputs = load_terraform_outputs(tf_dir)

    tf_api_url = _extract_tf_value(tf_outputs.get("api_url"))
    tf_registry = _extract_tf_value(tf_outputs.get("rajee_registry_bucket_name"))
    tf_rajee_endpoint = _extract_tf_value(tf_outputs.get("rajee_endpoint"))
    tf_rale_authorizer_url = _extract_tf_value(tf_outputs.get("rale_authorizer_url"))
    tf_rale_router_url = _extract_tf_value(tf_outputs.get("rale_router_url"))

    server_url = (
        chosen.server_url
        or os.getenv("RAJA_SERVER_URL")
        or file_values.get("RAJA_SERVER_URL")
        or file_values.get("server_url")
        or tf_api_url
        or DEFAULT_SERVER_URL
    )
    registry = _normalize_registry(
        chosen.registry
        or os.getenv("RAJA_REGISTRY")
        or file_values.get("RAJA_REGISTRY")
        or file_values.get("registry")
        or tf_registry
    )
    rajee_endpoint = (
        chosen.rajee_endpoint
        or os.getenv("RAJEE_ENDPOINT")
        or file_values.get("RAJEE_ENDPOINT")
        or file_values.get("rajee_endpoint")
        or tf_rajee_endpoint
        or DEFAULT_RAJEE_ENDPOINT
    )
    admin_key = (
        chosen.admin_key
        or os.getenv("RAJA_ADMIN_KEY")
        or file_values.get("RAJA_ADMIN_KEY")
        or file_values.get("admin_key")
        or ""
    )
    principal = (
        chosen.principal
        or os.getenv("RAJA_PRINCIPAL")
        or file_values.get("RAJA_PRINCIPAL")
        or file_values.get("principal")
        or DEFAULT_PRINCIPAL
    )

    rale_authorizer_url = (
        os.getenv("RALE_AUTHORIZER_URL")
        or file_values.get("RALE_AUTHORIZER_URL")
        or file_values.get("rale_authorizer_url")
        or tf_rale_authorizer_url
        or ""
    )
    rale_router_url = (
        os.getenv("RALE_ROUTER_URL")
        or file_values.get("RALE_ROUTER_URL")
        or file_values.get("rale_router_url")
        or tf_rale_router_url
        or ""
    )

    return (
        ResolvedConfig(
            server_url=server_url.rstrip("/"),
            registry=registry,
            rajee_endpoint=rajee_endpoint.rstrip("/"),
            admin_key=admin_key,
            principal=principal,
            tf_dir=tf_dir,
            rale_authorizer_url=rale_authorizer_url.rstrip("/") if rale_authorizer_url else "",
            rale_router_url=rale_router_url.rstrip("/") if rale_router_url else "",
        ),
        tf_outputs,
    )


def validate_config(config: ResolvedConfig) -> list[str]:
    errors: list[str] = []
    if not config.registry:
        errors.append("RAJA_REGISTRY is required (flag/env/config/terraform output)")
    if not config.admin_key:
        errors.append("RAJA_ADMIN_KEY is required (flag/env/config)")
    if not config.server_url:
        errors.append("RAJA_SERVER_URL is required (flag/env/config/terraform output: api_url)")
    if not config.rajee_endpoint:
        errors.append("RAJEE_ENDPOINT is required (terraform output: rajee_endpoint)")
    if not config.rale_authorizer_url:
        errors.append("RALE_AUTHORIZER_URL is required (terraform output: rale_authorizer_url)")
    if not config.rale_router_url:
        errors.append("RALE_ROUTER_URL is required (terraform output: rale_router_url)")
    return errors
