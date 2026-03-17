from __future__ import annotations

import sys

import click

from raja.rale.config import (
    ConfigOverrides,
    resolve_config,
    resolve_mode,
    validate_config,
)
from raja.rale.console import Console
from raja.rale.runner import run_all
from raja.rale.state import SessionState


@click.group(invoke_without_command=True)
@click.option("--auto", "requested_auto", is_flag=True, help="Run phases without pauses")
@click.option("--manual", "requested_manual", is_flag=True, help="Pause between phases")
@click.option("--server-url", type=str, default=None, help="RAJA control-plane URL")
@click.option("--registry", type=str, default=None, help="Default Quilt registry")
@click.option("--rajee-endpoint", type=str, default=None, help="RAJEE endpoint URL")
@click.option("--admin-key", type=str, default=None, help="RAJA admin key")
@click.option("--tf-dir", type=str, default=None, help="Terraform directory for outputs")
@click.pass_context
def main(
    ctx: click.Context,
    requested_auto: bool,
    requested_manual: bool,
    server_url: str | None,
    registry: str | None,
    rajee_endpoint: str | None,
    admin_key: str | None,
    tf_dir: str | None,
) -> None:
    """RALE CLI demo runner."""
    console = Console()
    overrides = ConfigOverrides(
        server_url=server_url,
        registry=registry,
        rajee_endpoint=rajee_endpoint,
        admin_key=admin_key,
        tf_dir=tf_dir,
    )
    config, tf_outputs = resolve_config(overrides)
    ctx.obj = {
        "config": config,
        "tf_outputs": tf_outputs,
        "console": console,
        "requested_auto": requested_auto,
        "requested_manual": requested_manual,
    }

    if ctx.invoked_subcommand is not None:
        return

    try:
        mode = resolve_mode(
            requested_auto=requested_auto,
            requested_manual=requested_manual,
            stdin_is_tty=sys.stdin.isatty(),
        )
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc

    errors = validate_config(config)
    if errors:
        message = "\n".join(["Configuration errors:", *[f"- {error}" for error in errors]])
        raise click.ClickException(message)

    state = SessionState(config=config, tf_outputs=tf_outputs)
    try:
        run_all(state, mode, console)
    except RuntimeError as exc:
        raise click.ClickException(str(exc)) from exc


@main.command("check")
@click.pass_context
def check_command(ctx: click.Context) -> None:
    """Validate CLI configuration and print resolved values."""
    obj = ctx.obj or {}
    config = obj.get("config")
    if config is None:
        raise click.ClickException("Internal error: missing resolved config")

    console = obj.get("console", Console())
    console.print("Resolved configuration:")
    console.print(f"  server_url: {config.server_url}")
    console.print(f"  registry: {config.registry or '<missing>'}")
    console.print(f"  rajee_endpoint: {config.rajee_endpoint}")
    console.print(f"  principal: {config.principal}")
    console.print(f"  tf_dir: {config.tf_dir}")
    console.print(f"  admin_key: {'<set>' if config.admin_key else '<missing>'}")

    errors = validate_config(config)
    if errors:
        raise click.ClickException("; ".join(errors))

    console.print("Configuration check passed.")


if __name__ == "__main__":
    main()
