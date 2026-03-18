"""Integration tests verifying that seed_users.py correctly establishes DataZone
project membership and that find_project_for_principal resolves each seeded user.

Run with:
    pytest -m integration tests/integration/test_seed_users.py
"""

from __future__ import annotations

import os

import boto3
import pytest

from raja.datazone import DataZoneConfig, DataZoneService
from scripts.seed_config import build_user_assignments, load_seed_config
from scripts.tf_outputs import get_tf_output

from .helpers import require_raja_users

_DATAZONE_ENV_MAP = {
    "DATAZONE_DOMAIN_ID": "datazone_domain_id",
    "DATAZONE_SLOTS": "datazone_slots",
    "DATAZONE_PACKAGE_ASSET_TYPE": "datazone_package_asset_type",
    "DATAZONE_PACKAGE_ASSET_TYPE_REVISION": "datazone_package_asset_type_revision",
}


def _hydrate_datazone_env() -> None:
    for env_key, tf_key in _DATAZONE_ENV_MAP.items():
        if os.environ.get(env_key):
            continue
        value = get_tf_output(tf_key)
        if value:
            os.environ[env_key] = value


def _make_service() -> tuple[DataZoneService, DataZoneConfig]:
    _hydrate_datazone_env()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    config = DataZoneConfig.from_env()
    client = boto3.client("datazone", region_name=region)
    return DataZoneService(client=client, config=config), config


@pytest.mark.integration
def test_seeded_users_are_found_in_datazone_projects() -> None:
    """Every user in RAJA_USERS must resolve to a DataZone project."""
    principals = require_raja_users()
    service, config = _make_service()

    project_ids = [slot.project_id for _, slot in config.ordered_slots() if slot.project_id]

    assert project_ids, "No DataZone project IDs configured (check DATAZONE_SLOTS)"

    failures: list[str] = []
    for principal in principals:
        project_id = service.find_project_for_principal(principal, project_ids=project_ids)
        if project_id is None:
            failures.append(principal)

    assert not failures, (
        f"find_project_for_principal returned None for {len(failures)} principal(s) "
        f"— run `python scripts/seed_users.py` to fix:\n" + "\n".join(f"  {p}" for p in failures)
    )


@pytest.mark.integration
def test_seeded_users_land_in_correct_tiers() -> None:
    """RAJA_USERS are assigned round-robin across the configured logical projects."""
    raw_usernames = [u.strip() for u in os.environ.get("RAJA_USERS", "").split(",") if u.strip()]
    assert raw_usernames, "RAJA_USERS must be populated for seed validation"
    seed_config = load_seed_config()
    assignments = build_user_assignments(raw_usernames, seed_config)
    service, config = _make_service()
    project_ids = seed_config.project_id_map(config)

    failures: list[str] = []
    principals = require_raja_users()
    for assignment, principal in zip(assignments, principals, strict=False):
        project_id = project_ids.get(assignment.project_key, "")
        if not project_id:
            failures.append(
                f"{principal}: missing project ID for logical project {assignment.project_key}"
            )
            continue
        if not service._is_project_member(project_id=project_id, principal=principal):
            failures.append(
                f"{principal}: expected membership in {assignment.project_key} ({project_id})"
            )

    assert not failures, "Seeded principals landed in unexpected projects:\n" + "\n".join(
        f"  {failure}" for failure in failures
    )


@pytest.mark.integration
def test_unknown_principal_returns_none() -> None:
    """A principal that was never seeded must return None (fail-closed)."""
    service, config = _make_service()

    # Use an account that definitely does not exist
    fake_arn = "arn:aws:iam::000000000000:user/nobody-fake-xyzzy"
    project_ids = [slot.project_id for _, slot in config.ordered_slots() if slot.project_id]

    result = service.find_project_for_principal(fake_arn, project_ids=project_ids)
    assert result is None, (
        f"Expected None for unknown principal, got {result!r} — "
        "DataZone should not match a non-existent user to any project"
    )
