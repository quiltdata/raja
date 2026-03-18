#!/usr/bin/env python3
"""Seed IAM users into the configured DataZone test projects."""

from __future__ import annotations

import os
import sys

import boto3

from raja.datazone import DataZoneConfig, DataZoneService, datazone_enabled
from scripts.seed_config import (
    build_user_assignments,
    load_seed_config,
    load_seed_state,
    write_seed_state,
)
from scripts.tf_outputs import get_tf_output

_MIN_USERS = 3


def _get_region() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
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
            f"need at least {_MIN_USERS} to cover the configured project ring. "
            "Projects without an assigned principal will remain empty.",
            file=sys.stderr,
        )
    return users


def _get_raja_guests() -> list[str]:
    raw = os.environ.get("RAJA_GUESTS", "").strip()
    if not raw:
        return []
    return [u.strip() for u in raw.split(",") if u.strip()]


def _user_to_arn(username: str, account_id: str) -> str:
    return f"arn:aws:iam::{account_id}:user/{username}"


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    region = _get_region()
    _hydrate_datazone_env()
    usernames = _get_raja_users()
    guests = _get_raja_guests()
    seed_config = load_seed_config()
    account_id = _get_account_id(region)

    print(f"{'=' * 60}")
    print(
        f"Seeding {len(usernames)} RAJA user(s) + {len(guests)} guest(s) from account {account_id}"
    )
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

    project_ids = seed_config.project_id_map(datazone_config)
    assignments = build_user_assignments(usernames, seed_config)
    success_count = 0
    fail_count = 0
    project_principals: dict[str, list[str]] = {project.key: [] for project in seed_config.projects}

    for idx, assignment in enumerate(assignments):
        username = assignment.username
        arn = _user_to_arn(username, account_id)
        project = seed_config.project(assignment.project_key)
        project_id = project_ids.get(project.key, "")

        print(
            f"[{idx + 1}/{len(usernames)}] {username}"
            f"  logical_project={project.display_name}"
            + (f"  project={project_id}" if project_id else "")
        )

        if dry_run:
            print(
                f"  [DRY-RUN] Would add: {arn} → project={project_id} as {assignment.designation}"
            )
            success_count += 1
            continue

        if not project_id:
            print(f"  ⚠ No project ID for logical project {project.key}, skipping", file=sys.stderr)
            fail_count += 1
            continue

        try:
            datazone_service.ensure_project_membership(
                project_id=project_id,
                user_identifier=arn,
                designation=assignment.designation,
            )
            print(f"  ✓ DataZone: added {arn} to project {project_id} as {assignment.designation}")
            project_principals[project.key].append(arn)
            success_count += 1
        except Exception as e:
            print(f"  ✗ DataZone: membership failed for {arn}: {e}", file=sys.stderr)
            fail_count += 1

    # Preserve RAJA_GUESTS as optional overflow into the last configured project.
    if guests:
        overflow_project = seed_config.projects[-1]
        overflow_project_id = project_ids.get(overflow_project.key, "")
        print(
            f"\nSeeding {len(guests)} extra principal(s) → "
            f"{overflow_project.display_name} ({overflow_project_id})"
        )
        for idx, username in enumerate(guests):
            arn = _user_to_arn(username, account_id)
            print(
                f"[{idx + 1}/{len(guests)}] {username}"
                f"  logical_project={overflow_project.display_name}"
                f"  project={overflow_project_id}"
            )
            if dry_run:
                print(
                    f"  [DRY-RUN] Would add: {arn} → project={overflow_project_id} "
                    "as PROJECT_CONTRIBUTOR"
                )
                success_count += 1
                continue
            if not overflow_project_id:
                print("  ⚠ No overflow project ID, skipping", file=sys.stderr)
                fail_count += 1
                continue
            try:
                datazone_service.ensure_project_membership(
                    project_id=overflow_project_id,
                    user_identifier=arn,
                    designation="PROJECT_CONTRIBUTOR",
                )
                print(f"  ✓ DataZone: added {arn} to project {overflow_project_id}")
                project_principals[overflow_project.key].append(arn)
                success_count += 1
            except Exception as e:
                print(f"  ✗ DataZone: membership failed for {arn}: {e}", file=sys.stderr)
                fail_count += 1

    if not dry_run:
        state = load_seed_state()
        existing_projects = state.get("projects", {})
        if not isinstance(existing_projects, dict):
            existing_projects = {}
        state["default_project"] = seed_config.default_project
        state["default_principal"] = next(
            (
                principals[0]
                for project_key, principals in project_principals.items()
                if project_key == seed_config.default_project and principals
            ),
            next(
                (
                    principal
                    for principals in project_principals.values()
                    for principal in principals
                ),
                "",
            ),
        )
        state["projects"] = {
            project.key: {
                **(
                    existing_projects.get(project.key, {})
                    if isinstance(existing_projects.get(project.key, {}), dict)
                    else {}
                ),
                "display_name": project.display_name,
                "slot": project.slot,
                "project_id": project_ids.get(project.key, ""),
                "principals": project_principals.get(project.key, []),
            }
            for project in seed_config.projects
        }
        write_seed_state(state)

    total = len(usernames) + len(guests)
    print(f"\n{'=' * 60}")
    if dry_run:
        print(f"✓ DRY-RUN: Would seed {total} principal(s)")
    else:
        print(f"✓ Seeded {success_count}/{total} principal(s) successfully")
        if fail_count > 0:
            print(f"✗ Failed: {fail_count}")
    print(f"{'=' * 60}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
