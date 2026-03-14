#!/usr/bin/env python3
"""Seed real IAM users into DataZone projects for integration testing.

Users are read from RAJA_USERS (comma-separated usernames) and assigned to the
three DataZone project tiers:
  - Tier 0 (owner):  first user  → owner_project  (scopes: ["*:*:*"])
  - Tier 1 (users):  second user → users_project  (scopes: ["Package:*:write"])
  - Tier 2 (guests): remaining   → guests_project (scopes: ["Package:*:read"])

At least 3 users are needed to cover all tiers; a warning is printed if fewer
are provided.
"""

from __future__ import annotations

import os
import sys

import boto3

from raja.datazone import DataZoneConfig, DataZoneService, datazone_enabled
from scripts.tf_outputs import get_tf_output

_MIN_USERS = 3

_TIER_SCOPES: list[list[str]] = [
    ["*:*:*"],              # owner  — wildcard → owner_project
    ["Package:*:write"],    # users  — write    → users_project
    ["Package:*:read"],     # guests — read     → guests_project
]

_TIER_DESIGNATION = ["PROJECT_OWNER", "PROJECT_CONTRIBUTOR", "PROJECT_CONTRIBUTOR"]

_TIER_PROJECT_ENV = [
    "DATAZONE_OWNER_PROJECT_ID",
    "DATAZONE_USERS_PROJECT_ID",
    "DATAZONE_GUESTS_PROJECT_ID",
]


def _get_region() -> str:
    region = (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )
    if not region:
        print("✗ AWS_REGION environment variable is required", file=sys.stderr)
        sys.exit(1)
    return region


def _hydrate_datazone_env() -> None:
    mapping = {
        "DATAZONE_DOMAIN_ID": "datazone_domain_id",
        "DATAZONE_OWNER_PROJECT_ID": "datazone_owner_project_id",
        "DATAZONE_USERS_PROJECT_ID": "datazone_users_project_id",
        "DATAZONE_GUESTS_PROJECT_ID": "datazone_guests_project_id",
        "DATAZONE_PACKAGE_ASSET_TYPE": "datazone_package_asset_type",
        "DATAZONE_PACKAGE_ASSET_TYPE_REVISION": "datazone_package_asset_type_revision",
    }
    for env_key, output_key in mapping.items():
        if os.environ.get(env_key):
            continue
        value = get_tf_output(output_key)
        if value:
            os.environ[env_key] = value


def _get_account_id(region: str) -> str:
    sts = boto3.client("sts", region_name=region)
    return str(sts.get_caller_identity()["Account"])


def _get_raja_users() -> list[str]:
    raw = os.environ.get("RAJA_USERS", "").strip()
    if not raw:
        print("✗ RAJA_USERS is not set (comma-separated IAM usernames required)", file=sys.stderr)
        sys.exit(1)
    users = [u.strip() for u in raw.split(",") if u.strip()]
    if not users:
        print("✗ RAJA_USERS contains no valid usernames", file=sys.stderr)
        sys.exit(1)
    if len(users) < _MIN_USERS:
        print(
            f"⚠ Warning: only {len(users)} user(s) in RAJA_USERS; "
            f"need at least {_MIN_USERS} to cover all DataZone project tiers "
            f"(owner / users / guests). "
            f"Extra tiers will reuse the last user.",
            file=sys.stderr,
        )
    return users


def _user_to_arn(username: str, account_id: str) -> str:
    return f"arn:aws:iam::{account_id}:user/{username}"


def _tier_project_id(tier: int, config: DataZoneConfig) -> str:
    project_ids = [
        config.owner_project_id,
        config.users_project_id,
        config.guests_project_id,
    ]
    return project_ids[tier]


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    region = _get_region()
    _hydrate_datazone_env()
    usernames = _get_raja_users()
    account_id = _get_account_id(region)

    print(f"{'=' * 60}")
    print(f"Seeding {len(usernames)} RAJA user(s) from account {account_id}")
    print(f"Region: {region}")
    if dry_run:
        print("Mode:   DRY-RUN (no changes will be made)")
    print(f"{'=' * 60}\n")

    if not datazone_enabled():
        print("✗ DataZone is not enabled (DATAZONE_DOMAIN_ID not set)", file=sys.stderr)
        sys.exit(1)

    try:
        datazone_config = DataZoneConfig.from_env()
        datazone_client = boto3.client("datazone", region_name=region)
        datazone_service = DataZoneService(client=datazone_client, config=datazone_config)
        print(f"DataZone domain: {datazone_config.domain_id}\n")
    except Exception as e:
        print(f"✗ Failed to initialise DataZone: {e}", file=sys.stderr)
        sys.exit(1)

    success_count = 0
    fail_count = 0

    for idx, username in enumerate(usernames):
        tier = min(idx, len(_TIER_SCOPES) - 1)
        arn = _user_to_arn(username, account_id)
        designation = _TIER_DESIGNATION[tier]
        tier_name = ["owner", "users", "guests"][tier]
        project_id = _tier_project_id(tier, datazone_config)

        print(
            f"[{idx + 1}/{len(usernames)}] {username}"
            f"  tier={tier_name}"
            + (f"  project={project_id}" if project_id else "")
        )

        if dry_run:
            print(f"  [DRY-RUN] Would add: {arn} → project={project_id} as {designation}")
            success_count += 1
            continue

        if not project_id:
            print(f"  ⚠ No project ID for tier {tier_name}, skipping", file=sys.stderr)
            fail_count += 1
            continue

        try:
            datazone_service.ensure_project_membership(
                project_id=project_id,
                user_identifier=arn,
                designation=designation,
            )
            print(f"  ✓ DataZone: added {arn} to project {project_id} as {designation}")
            success_count += 1
        except Exception as e:
            print(f"  ✗ DataZone: membership failed for {arn}: {e}", file=sys.stderr)
            fail_count += 1

    print(f"\n{'=' * 60}")
    if dry_run:
        print(f"✓ DRY-RUN: Would seed {len(usernames)} user(s)")
    else:
        print(f"✓ Seeded {success_count}/{len(usernames)} user(s) successfully")
        if fail_count > 0:
            print(f"✗ Failed: {fail_count}")
    print(f"{'=' * 60}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
