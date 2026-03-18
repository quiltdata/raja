from __future__ import annotations

import json
from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx

from raja.quilt_uri import QuiltUri

from .authorize import run_authorize
from .console import Console, Table
from .fetch import run_fetch
from .state import SessionState


@dataclass(frozen=True)
class AccessPackageAudit:
    package_name: str
    owner_project_id: str
    owner_project_name: str
    access_mode: str
    sample_path: str
    usl: str


def _load_seed_state() -> dict[str, Any]:
    for root in [Path.cwd(), *Path.cwd().parents]:
        path = root / ".rale-seed-state.json"
        if not path.is_file():
            continue
        payload = json.loads(path.read_text())
        if isinstance(payload, dict):
            return payload
    return {}


def _load_quilt3() -> Any:
    try:
        return import_module("quilt3")
    except Exception as exc:
        raise RuntimeError("quilt3 is required for access auditing") from exc


def _admin_get_json(
    *,
    server_url: str,
    admin_key: str,
    path: str,
    query: dict[str, str] | None = None,
) -> dict[str, Any]:
    url = f"{server_url.rstrip('/')}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{urlencode(query)}"
    try:
        response = httpx.get(
            url,
            headers={"Authorization": f"Bearer {admin_key}"},
            timeout=30.0,
        )
    except httpx.RequestError as exc:
        raise RuntimeError(f"RAJA server not reachable at {server_url}") from exc
    if response.status_code >= 400:
        raise RuntimeError(f"{path} failed with status {response.status_code}: {response.text}")
    payload = response.json()
    if not isinstance(payload, dict):
        raise RuntimeError(f"{path} returned invalid JSON")
    return payload


def _build_sample_usl(*, registry: str, package_name: str) -> tuple[str, str]:
    quilt3 = _load_quilt3()
    try:
        package = quilt3.Package.browse(name=package_name, registry=registry)
    except Exception as exc:
        raise RuntimeError(f"Cannot browse package {package_name} in {registry}") from exc

    manifest_hash = getattr(package, "top_hash", None)
    if not isinstance(manifest_hash, str) or not manifest_hash:
        raise RuntimeError(f"Cannot resolve latest hash for package {package_name}")

    entries = list(package.walk())
    if not entries:
        raise RuntimeError(f"Package {package_name} has no files")

    logical_path = str(entries[0][0])
    registry_bucket = registry.split("://", 1)[1] if "://" in registry else registry
    usl = QuiltUri(
        storage="s3",
        registry=registry_bucket,
        package_name=package_name,
        hash=manifest_hash,
        path=logical_path,
    ).normalized()
    return logical_path, usl


def _build_seeded_sample_usl(uri: str) -> tuple[str, str]:
    sample_path = "README.md"
    separator = "&" if "#" in uri else "#"
    return sample_path, f"{uri}{separator}path={sample_path}"


def _collect_seeded_access_audits(
    state: SessionState,
) -> tuple[list[str], list[AccessPackageAudit]] | None:
    seed_state = _load_seed_state()
    projects = seed_state.get("projects")
    packages = seed_state.get("packages")
    if not isinstance(projects, dict) or not isinstance(packages, dict):
        return None

    principal_project_key = ""
    principal_project: dict[str, Any] | None = None
    for project_key, raw_project in projects.items():
        if not isinstance(raw_project, dict):
            continue
        principals = raw_project.get("principals", [])
        if isinstance(principals, list) and state.config.principal in principals:
            principal_project_key = str(project_key)
            principal_project = raw_project
            break
    if principal_project is None:
        return None

    project_names = [str(principal_project.get("display_name") or principal_project_key)]
    project_package_keys = [
        ("home_package", "OWNED"),
        ("foreign_package", "GRANTED"),
        ("inaccessible_package", "INACCESSIBLE"),
    ]
    audits: list[AccessPackageAudit] = []
    for package_key, access_mode in project_package_keys:
        package_name = str(principal_project.get(package_key) or "")
        if not package_name:
            raise RuntimeError(f"seed state missing {package_key} for principal project")
        package_state = packages.get(package_name, {})
        if not isinstance(package_state, dict):
            raise RuntimeError(f"seed state missing package entry for {package_name}")
        producer_project_key = str(package_state.get("producer_project") or "")
        producer_project = projects.get(producer_project_key, {})
        producer_name = (
            str(producer_project.get("display_name") or producer_project_key)
            if isinstance(producer_project, dict)
            else producer_project_key
        )
        uri = str(package_state.get("uri") or "")
        if not uri:
            raise RuntimeError(f"seed state missing uri for {package_name}")
        sample_path, usl = _build_seeded_sample_usl(uri)
        audits.append(
            AccessPackageAudit(
                package_name=package_name,
                owner_project_id=str(producer_project.get("project_id") or producer_project_key)
                if isinstance(producer_project, dict)
                else producer_project_key,
                owner_project_name=producer_name,
                access_mode=access_mode,
                sample_path=sample_path,
                usl=usl,
            )
        )
    return project_names, audits


def _collect_access_audits(state: SessionState) -> tuple[list[str], list[AccessPackageAudit]]:
    seeded = _collect_seeded_access_audits(state)
    if seeded is not None:
        return seeded

    payload = _admin_get_json(
        server_url=state.config.server_url,
        admin_key=state.config.admin_key,
        path="/admin/access-graph",
        query={"principal": state.config.principal},
    )

    summaries = payload.get("principal_summary", [])
    if not isinstance(summaries, list) or not summaries:
        raise RuntimeError(f"Principal not found in DataZone projects: {state.config.principal}")
    summary = summaries[0]
    project_ids = [str(item) for item in summary.get("project_ids", []) if str(item)]
    project_names = [str(item) for item in summary.get("project_names", []) if str(item)]
    if not project_ids:
        raise RuntimeError(f"Principal has no project memberships: {state.config.principal}")

    packages = payload.get("packages", [])
    access_rows = payload.get("access", [])
    if not isinstance(packages, list) or not isinstance(access_rows, list):
        raise RuntimeError("/admin/access-graph returned invalid package data")

    access_mode_by_package: dict[str, str] = {}
    for row in access_rows:
        if not isinstance(row, dict):
            continue
        package_name = str(row.get("package_name") or "")
        project_id = str(row.get("principal_project_id") or "")
        access_mode = str(row.get("access_mode") or "")
        if package_name and project_id in project_ids and access_mode:
            access_mode_by_package[package_name] = access_mode

    audits: list[AccessPackageAudit] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        package_name = str(package.get("package_name") or "")
        if not package_name:
            continue
        sample_path, usl = _build_sample_usl(
            registry=state.config.registry,
            package_name=package_name,
        )
        audits.append(
            AccessPackageAudit(
                package_name=package_name,
                owner_project_id=str(package.get("owner_project_id") or ""),
                owner_project_name=str(package.get("owner_project_name") or ""),
                access_mode=access_mode_by_package.get(package_name, "INACCESSIBLE"),
                sample_path=sample_path,
                usl=usl,
            )
        )

    if not audits:
        raise RuntimeError("No package listings available for access audit")
    return project_names, audits


def run_access_audit(state: SessionState, console: Console) -> None:
    project_names, audits = _collect_access_audits(state)

    console.rule("RALE CLI - ACCESS AUDIT")
    console.print(f"Principal: [bold]{state.config.principal}[/bold]")
    console.print(f"Projects: [bold]{', '.join(project_names)}[/bold]")

    package_table = Table(title="Package access")
    package_table.add_column("Package")
    package_table.add_column("Owner")
    package_table.add_column("Access")
    package_table.add_column("Sample file")
    for audit in audits:
        package_table.add_row(
            audit.package_name,
            audit.owner_project_name or audit.owner_project_id,
            audit.access_mode,
            audit.sample_path,
        )
    console.print(package_table)

    result_table = Table(title="Access verification")
    result_table.add_column("Package")
    result_table.add_column("Expected")
    result_table.add_column("Result")

    for audit in audits:
        probe_state = SessionState(config=state.config, tf_outputs=state.tf_outputs, usl=audit.usl)
        if audit.access_mode in {"OWNED", "GRANTED"}:
            run_authorize(state=probe_state, console=console)
            run_fetch(state=probe_state, console=console)
            result_table.add_row(audit.package_name, audit.access_mode, "ALLOW")
            continue

        try:
            run_authorize(state=probe_state, console=console)
        except RuntimeError as exc:
            if "DENY" not in str(exc):
                raise RuntimeError(
                    f"Expected DENY for inaccessible package {audit.package_name}, got: {exc}"
                ) from exc
            result_table.add_row(audit.package_name, "INACCESSIBLE", "DENY")
            continue
        raise RuntimeError(
            "Expected inaccessible package "
            f"{audit.package_name} to be denied, but authorize succeeded"
        )

    console.print(result_table)
