"""Integration tests verifying DataZone package grants for the seeded test package.

Checks that:
- The owner project has inherent access as the asset producer.
- The users and guests projects have accepted subscription grants.
- Every principal in RAJA_USERS resolves to a project that has access.
- An unknown principal is denied.

Run with:
    pytest -m integration tests/integration/test_package_grant.py -v
"""

from __future__ import annotations

import os

import boto3
import pytest

from raja.datazone import DataZoneConfig, DataZoneService

from .helpers import require_raja_users, require_rale_test_quilt_uri
from .test_seed_users import _hydrate_datazone_env


def _make_service() -> tuple[DataZoneService, DataZoneConfig]:
    _hydrate_datazone_env()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    config = DataZoneConfig.from_env()
    client = boto3.client("datazone", region_name=region)
    return DataZoneService(client=client, config=config), config


@pytest.mark.integration
def test_package_listing_exists() -> None:
    """The seeded package must have a DataZone listing."""
    uri = require_rale_test_quilt_uri()
    service, _ = _make_service()

    listing = service.find_package_listing(uri)
    assert listing is not None, (
        f"No DataZone listing found for {uri}\nRun: python scripts/seed_packages.py"
    )
    assert listing.name == "demo/package-grant"
    assert listing.listing_id
    assert listing.owner_project_id


@pytest.mark.integration
def test_owner_project_has_package_grant() -> None:
    """Owner project has inherent access as the asset producer — no subscription needed."""
    uri = require_rale_test_quilt_uri()
    service, config = _make_service()

    assert config.owner_project_id, "DATAZONE_OWNER_PROJECT_ID not set"
    assert service.has_package_grant(project_id=config.owner_project_id, quilt_uri=uri), (
        f"Owner project {config.owner_project_id} should have inherent access to {uri}"
    )


@pytest.mark.integration
def test_users_project_has_package_grant() -> None:
    """Users project must have an accepted subscription grant to the seeded package."""
    uri = require_rale_test_quilt_uri()
    service, config = _make_service()

    assert config.users_project_id, "DATAZONE_USERS_PROJECT_ID not set"
    assert service.has_package_grant(project_id=config.users_project_id, quilt_uri=uri), (
        f"Users project {config.users_project_id} has no accepted grant for {uri}\n"
        "Run: python scripts/seed_packages.py"
    )


@pytest.mark.integration
def test_guests_project_has_package_grant() -> None:
    """Guests project must have an accepted subscription grant to the seeded package."""
    uri = require_rale_test_quilt_uri()
    service, config = _make_service()

    assert config.guests_project_id, "DATAZONE_GUESTS_PROJECT_ID not set"
    assert service.has_package_grant(project_id=config.guests_project_id, quilt_uri=uri), (
        f"Guests project {config.guests_project_id} has no accepted grant for {uri}\n"
        "Run: python scripts/seed_packages.py"
    )


@pytest.mark.integration
def test_all_seeded_principals_can_access_package() -> None:
    """End-to-end: every RAJA_USERS principal must resolve to a project with package access."""
    uri = require_rale_test_quilt_uri()
    principals = require_raja_users()
    service, config = _make_service()

    project_ids = [
        p
        for p in [
            config.owner_project_id,
            config.users_project_id,
            config.guests_project_id,
        ]
        if p
    ]

    failures: list[str] = []
    for principal in principals:
        project_id = service.find_project_for_principal(principal, project_ids=project_ids)
        if project_id is None:
            failures.append(f"{principal}: not a member of any project (run seed_users.py)")
            continue
        if not service.has_package_grant(project_id=project_id, quilt_uri=uri):
            failures.append(
                f"{principal}: in project {project_id} but no grant for {uri} "
                "(run seed_packages.py)"
            )

    assert not failures, f"{len(failures)} principal(s) cannot access the package:\n" + "\n".join(
        f"  {f}" for f in failures
    )


@pytest.mark.integration
def test_unknown_principal_denied_package() -> None:
    """A principal in no project must not pass the package grant check."""
    service, config = _make_service()

    project_ids = [
        p
        for p in [
            config.owner_project_id,
            config.users_project_id,
            config.guests_project_id,
        ]
        if p
    ]

    fake_arn = "arn:aws:iam::000000000000:user/nobody-fake-xyzzy"
    project_id = service.find_project_for_principal(fake_arn, project_ids=project_ids)
    assert project_id is None, f"Unknown principal unexpectedly resolved to project {project_id}"
