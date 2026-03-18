from __future__ import annotations

from importlib import import_module
from typing import Any, cast

import click

from raja.quilt_uri import QuiltUri

from .config import DEFAULT_PRINCIPAL
from .console import Console, Table
from .state import RunMode, SessionState


def _load_quilt3() -> Any:
    try:
        quilt3 = import_module("quilt3")
    except Exception as exc:
        raise RuntimeError("quilt3 is required for package selection") from exc
    return quilt3


def _select_index(*, count: int, mode: RunMode, label: str) -> int:
    if count == 0:
        raise RuntimeError(f"No {label} available")
    if mode == "auto":
        return 0

    selected = cast(int, click.prompt(f"Choose a {label} [1-{count}]", type=int, default=1))
    if selected < 1 or selected > count:
        raise RuntimeError(f"Invalid {label} selection: {selected}")
    return selected - 1


def _sorted_packages_for_principal(packages: list[str], principal: str) -> list[str]:
    preferred: dict[str, int] = {}
    for index, package_name in enumerate(("alpha/home", "bio/home", "compute/home")):
        if package_name in packages:
            preferred[package_name] = index
    if principal == DEFAULT_PRINCIPAL and "demo/e2e" in packages:
        preferred["demo/e2e"] = len(preferred)

    fallback_priority = len(preferred) + 1
    return sorted(
        packages,
        key=lambda package: (preferred.get(package, fallback_priority), package),
    )


def run_select(state: SessionState, mode: RunMode, console: Console) -> None:
    quilt3 = _load_quilt3()
    registry = state.config.registry

    console.rule("RALE CLI - SETUP")
    console.print(f"Registry: [bold]{registry}[/bold]")
    console.print(f"RAJEE endpoint: [bold]{state.config.rajee_endpoint}[/bold]")

    packages = _sorted_packages_for_principal(
        sorted(quilt3.list_packages(registry=registry)),
        state.config.principal,
    )
    if not packages:
        raise RuntimeError("Package not found in registry")

    package_table = Table(title="Available packages")
    package_table.add_column("#", justify="right")
    package_table.add_column("Package")
    for idx, package in enumerate(packages, start=1):
        package_table.add_row(str(idx), package)
    console.print(package_table)

    package_index = _select_index(count=len(packages), mode=mode, label="package")
    package_name = packages[package_index]

    try:
        package = quilt3.Package.browse(name=package_name, registry=registry)
    except Exception as exc:
        raise RuntimeError("Cannot resolve latest hash - check registry access") from exc

    manifest_hash = getattr(package, "top_hash", None)
    if not isinstance(manifest_hash, str) or not manifest_hash:
        raise RuntimeError("Cannot resolve latest hash - check registry access")

    entries = list(package.walk())
    if not entries:
        raise RuntimeError("Selected package has no files")

    file_table = Table(title=f"Files in {package_name}@{manifest_hash}")
    file_table.add_column("#", justify="right")
    file_table.add_column("Path")
    file_table.add_column("Bytes", justify="right")

    logical_paths: list[str] = []
    for idx, (logical_path, entry) in enumerate(entries, start=1):
        logical_paths.append(str(logical_path))
        size_value = getattr(entry, "size", None)
        size_text = str(size_value) if isinstance(size_value, int) else "-"
        file_table.add_row(str(idx), str(logical_path), size_text)

    console.print(file_table)
    file_index = _select_index(count=len(logical_paths), mode=mode, label="file")
    logical_path = logical_paths[file_index]

    registry_bucket = registry.split("://", 1)[1] if "://" in registry else registry
    usl = QuiltUri(
        storage="s3",
        registry=registry_bucket,
        package_name=package_name,
        hash=manifest_hash,
        path=logical_path,
    ).normalized()

    state.package_name = package_name
    state.manifest_hash = manifest_hash
    state.logical_path = logical_path
    state.usl = usl

    console.print("\n[bold]Quilt+ URI (USL):[/bold]")
    console.print(usl)
