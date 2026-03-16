#!/usr/bin/env python3
"""Fill known SageMaker Unified Studio / DataZone V2 deployment gaps.

This script patches the current Terraform provider gaps for RAJA's V2 domain:

1. Ensure the default project profile exists and is ENABLED.
2. Ensure the owner/users/guests projects exist in the V2 domain.
3. Ensure the root domain unit grants CREATE_ASSET_TYPE to owner projects.
4. Refresh infra/tf-outputs.json with discovered project IDs.

It is safe to rerun. Existing resources are reused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tf_outputs import load_tf_outputs

OUTPUTS_PATH = REPO_ROOT / "infra" / "tf-outputs.json"
ENV_PATH = REPO_ROOT / ".env"

DEFAULT_PROFILE_NAME = "raja-default-profile"
DEFAULT_PROFILE_DESCRIPTION = "Default project profile for RAJA Terraform-managed V2 projects"
PROJECT_SPECS = {
    "owner": {
        "name": "raja-owner",
        "description": (
            "Publishes QuiltPackage asset listings; RAJA control plane creates listings "
            "here and accepts subscriber requests on behalf of principals"
        ),
    },
    "users": {
        "name": "raja-users",
        "description": (
            "Subscriber project for authenticated principals; principals are added as "
            "members by the control plane"
        ),
    },
    "guests": {
        "name": "raja-guests",
        "description": (
            "Subscriber project for unauthenticated/public read-only access; "
            "subscriptions are auto-approved"
        ),
    },
}


@dataclass
class Context:
    region: str
    domain_id: str
    domain_name: str
    root_domain_unit_id: str
    project_profile_name: str
    dry_run: bool


def _load_dotenv() -> None:
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show planned actions only.")
    parser.add_argument("--domain-id", help="Override domain ID instead of reading tf outputs.")
    parser.add_argument(
        "--project-profile-name",
        default=DEFAULT_PROFILE_NAME,
        help=f"Project profile name to ensure. Default: {DEFAULT_PROFILE_NAME}",
    )
    return parser.parse_args()


def _get_region() -> str:
    return (
        os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or "us-east-1"
    )


def _get_domain_id(cli_domain_id: str | None) -> str:
    if cli_domain_id:
        return cli_domain_id
    env_domain = os.environ.get("DATAZONE_DOMAIN_ID")
    if env_domain:
        return env_domain
    output_domain = load_tf_outputs().get("datazone_domain_id")
    if isinstance(output_domain, str) and output_domain:
        return output_domain
    print("ERROR: could not determine DataZone domain ID.", file=sys.stderr)
    sys.exit(1)


def _client(region: str) -> Any:
    return boto3.client("datazone", region_name=region)


def _list_all(method: Any, key: str, **kwargs: Any) -> list[dict[str, Any]]:
    paginator = method.__self__.get_paginator(method.__name__)
    items: list[dict[str, Any]] = []
    for page in paginator.paginate(**kwargs):
        page_items = page.get(key, [])
        if isinstance(page_items, list):
            items.extend(page_items)
    return items


def _get_context(args: argparse.Namespace) -> Context:
    region = _get_region()
    client = _client(region)
    domain_id = _get_domain_id(args.domain_id)
    domain = client.get_domain(identifier=domain_id)
    return Context(
        region=region,
        domain_id=domain_id,
        domain_name=str(domain["name"]),
        root_domain_unit_id=str(domain["rootDomainUnitId"]),
        project_profile_name=args.project_profile_name,
        dry_run=bool(args.dry_run),
    )


def _ensure_project_profile(client: Any, ctx: Context) -> str:
    profiles = _list_all(
        client.list_project_profiles,
        "items",
        domainIdentifier=ctx.domain_id,
    )
    for profile in profiles:
        if profile.get("name") != ctx.project_profile_name:
            continue
        profile_id = str(profile["id"])
        status = str(profile.get("status", ""))
        print(f"Project profile: {ctx.project_profile_name} ({profile_id})")
        if status != "ENABLED":
            if ctx.dry_run:
                print("  [DRY-RUN] Would enable project profile")
            else:
                client.update_project_profile(
                    domainIdentifier=ctx.domain_id,
                    identifier=profile_id,
                    status="ENABLED",
                )
                print("  Enabled project profile")
        return profile_id

    print(f"Project profile: {ctx.project_profile_name} (missing)")
    if ctx.dry_run:
        print("  [DRY-RUN] Would create and enable project profile")
        return "dry-run-project-profile"

    created = client.create_project_profile(
        domainIdentifier=ctx.domain_id,
        domainUnitIdentifier=ctx.root_domain_unit_id,
        name=ctx.project_profile_name,
        description=DEFAULT_PROFILE_DESCRIPTION,
        status="ENABLED",
    )
    profile_id = str(created["id"])
    print(f"  Created project profile {profile_id}")
    return profile_id


def _list_projects(client: Any, ctx: Context) -> list[dict[str, Any]]:
    return _list_all(client.list_projects, "items", domainIdentifier=ctx.domain_id)


def _ensure_projects(client: Any, ctx: Context, profile_id: str) -> dict[str, str]:
    projects = _list_projects(client, ctx)
    by_name = {str(project["name"]): project for project in projects}
    ensured: dict[str, str] = {}

    for key, spec in PROJECT_SPECS.items():
        existing = by_name.get(spec["name"])
        if existing:
            project_id = str(existing["id"])
            ensured[key] = project_id
            print(f"Project {key}: {spec['name']} ({project_id})")
            continue

        print(f"Project {key}: {spec['name']} (missing)")
        if ctx.dry_run:
            print("  [DRY-RUN] Would create V2 project")
            ensured[key] = f"dry-run-{key}"
            continue

        created = client.create_project(
            domainIdentifier=ctx.domain_id,
            name=spec["name"],
            description=spec["description"],
            projectProfileId=profile_id,
        )
        project_id = str(created["id"])
        ensured[key] = project_id
        print(f"  Created project {project_id}")

    return ensured


def _ensure_asset_type_grant(client: Any, ctx: Context) -> None:
    grants = _list_all(
        client.list_policy_grants,
        "grantList",
        domainIdentifier=ctx.domain_id,
        entityIdentifier=ctx.root_domain_unit_id,
        entityType="DOMAIN_UNIT",
        policyType="CREATE_ASSET_TYPE",
    )
    for grant in grants:
        principal = grant.get("principal", {})
        project = principal.get("project", {})
        if project.get("projectDesignation") != "OWNER":
            continue
        project_filter = project.get("projectGrantFilter", {}).get("domainUnitFilter", {})
        if project_filter.get("domainUnit") != ctx.root_domain_unit_id:
            continue
        print(f"Asset type grant: present ({grant.get('grantId', 'unknown')})")
        return

    print("Asset type grant: missing")
    if ctx.dry_run:
        print("  [DRY-RUN] Would add CREATE_ASSET_TYPE grant on root domain unit")
        return

    response = client.add_policy_grant(
        domainIdentifier=ctx.domain_id,
        entityIdentifier=ctx.root_domain_unit_id,
        entityType="DOMAIN_UNIT",
        policyType="CREATE_ASSET_TYPE",
        detail={"createAssetType": {"includeChildDomainUnits": False}},
        principal={
            "project": {
                "projectDesignation": "OWNER",
                "projectGrantFilter": {
                    "domainUnitFilter": {
                        "domainUnit": ctx.root_domain_unit_id,
                        "includeChildDomainUnits": False,
                    }
                },
            }
        },
    )
    print(f"  Added asset type grant {response['grantId']}")


def _update_outputs(project_ids: dict[str, str], ctx: Context) -> None:
    outputs = load_tf_outputs()
    updates = {
        "datazone_domain_id": ctx.domain_id,
        "datazone_owner_project_id": project_ids["owner"],
        "datazone_users_project_id": project_ids["users"],
        "datazone_guests_project_id": project_ids["guests"],
    }
    changed = False
    for key, value in updates.items():
        if outputs.get(key) == value:
            continue
        outputs[key] = value
        changed = True

    if not changed:
        print("Outputs: no changes")
        return
    if ctx.dry_run:
        print("Outputs: [DRY-RUN] Would refresh infra/tf-outputs.json")
        return

    OUTPUTS_PATH.write_text(json.dumps(outputs))
    print("Outputs: refreshed infra/tf-outputs.json")


def _print_import_hints(ctx: Context, project_ids: dict[str, str]) -> None:
    print("\nTerraform import hints:")
    for key, project_id in project_ids.items():
        print(
            "  terraform import aws_datazone_project."
            f"{key} {ctx.domain_id}:{project_id}"
        )


def main() -> None:
    _load_dotenv()
    args = _parse_args()

    try:
        ctx = _get_context(args)
        client = _client(ctx.region)
        print(f"Domain: {ctx.domain_name} ({ctx.domain_id})")
        print(f"Region: {ctx.region}")
        if ctx.dry_run:
            print("Mode:   DRY-RUN")
        profile_id = _ensure_project_profile(client, ctx)
        project_ids = _ensure_projects(client, ctx, profile_id)
        _ensure_asset_type_grant(client, ctx)
        _update_outputs(project_ids, ctx)
        _print_import_hints(ctx, project_ids)
    except ClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
