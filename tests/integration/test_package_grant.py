"""Integration tests verifying the ring-shaped package grant topology."""

from __future__ import annotations

import os

import boto3
import pytest

from raja.datazone import DataZoneConfig, DataZoneService

from .helpers import require_seed_state
from .test_seed_users import _hydrate_datazone_env


def _make_service() -> tuple[DataZoneService, DataZoneConfig]:
    _hydrate_datazone_env()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    config = DataZoneConfig.from_env()
    client = boto3.client("datazone", region_name=region)
    return DataZoneService(client=client, config=config), config


@pytest.mark.integration
def test_package_listings_exist() -> None:
    """Every configured seed package must have a DataZone listing."""
    state = require_seed_state()
    service, _ = _make_service()
    packages = state.get("packages", {})
    assert isinstance(packages, dict) and packages, "seed state packages are missing"

    failures: list[str] = []
    for package_name, package in packages.items():
        assert isinstance(package, dict)
        uri = str(package.get("uri") or "")
        listing = service.find_package_listing(uri)
        if listing is None:
            failures.append(f"{package_name}: missing listing for {uri}")
            continue
        if listing.name != package_name:
            failures.append(f"{package_name}: listing name mismatch ({listing.name})")
    assert not failures, "\n".join(failures)


@pytest.mark.integration
def test_each_project_has_home_and_foreign_but_not_inaccessible_package() -> None:
    state = require_seed_state()
    service, config = _make_service()

    projects = state.get("projects", {})
    packages = state.get("packages", {})
    assert isinstance(projects, dict) and isinstance(packages, dict)

    failures: list[str] = []
    for project_key, project in projects.items():
        assert isinstance(project, dict)
        project_id = str(project.get("project_id") or "")
        home_package_name = str(project.get("home_package") or "")
        foreign_package_name = str(project.get("foreign_package") or "")
        inaccessible_package_name = str(project.get("inaccessible_package") or "")
        if not project_id:
            failures.append(f"{project_key}: missing project_id")
            continue

        for access_label, package_name, expected in (
            ("home", home_package_name, True),
            ("foreign", foreign_package_name, True),
            ("inaccessible", inaccessible_package_name, False),
        ):
            package_state = packages.get(package_name, {})
            if not isinstance(package_state, dict):
                failures.append(f"{project_key}: missing package state for {package_name}")
                continue
            uri = str(package_state.get("uri") or "")
            allowed = service.has_package_grant(project_id=project_id, quilt_uri=uri)
            if allowed != expected:
                failures.append(
                    f"{project_key}: {access_label} package {package_name} "
                    f"expected allowed={expected}, got {allowed}"
                )

    assert not failures, "\n".join(failures)


@pytest.mark.integration
def test_unknown_principal_denied_package() -> None:
    """A principal in no project must not resolve to any package grant path."""
    service, config = _make_service()

    project_ids = [
        project.project_id for _, project in config.ordered_projects() if project.project_id
    ]

    fake_arn = "arn:aws:iam::000000000000:user/nobody-fake-xyzzy"
    project_id = service.find_project_for_principal(fake_arn, project_ids=project_ids)
    assert project_id is None, f"Unknown principal unexpectedly resolved to project {project_id}"
