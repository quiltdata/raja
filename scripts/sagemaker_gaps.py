#!/usr/bin/env python3
"""Fill known SageMaker Unified Studio / DataZone V2 deployment gaps.

This script patches the current Terraform provider gaps for RAJA's V2 domain:

1. Ensure the default project profile exists and is ENABLED.
2. Resolve the three configured seed projects in the V2 domain.
3. Ensure the root domain unit grants CREATE_ASSET_TYPE to project owners.
4. Refresh infra/tf-outputs.json with discovered project IDs.

It is safe to rerun. Existing resources are reused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.seed_config import load_seed_config  # noqa: E402
from scripts.tf_outputs import load_tf_outputs  # noqa: E402

OUTPUTS_PATH = REPO_ROOT / "infra" / "tf-outputs.json"
ENV_PATH = REPO_ROOT / ".env"

DEFAULT_PROFILE_NAME = "raja-default-profile"
DEFAULT_PROFILE_DESCRIPTION = "Default project profile for RAJA Terraform-managed V2 projects"
SEED_CONFIG = load_seed_config()
PROJECT_SPECS = {
    project.slot: {
        "name": project.display_name,
        "description": (
            f"{project.display_name} project in the RAJA symmetric seed topology. "
            "Each project produces one package, consumes one foreign package, "
            "and is denied one package."
        ),
    }
    for project in SEED_CONFIG.projects
}
ENVIRONMENT_SPECS = {
    project.slot: f"raja-{project.key}-env"
    for project in SEED_CONFIG.projects
}
CUSTOM_BLUEPRINT_CANDIDATE_NAMES = (
    "raja-registry-blueprint",
    "raja-poc",
    "CustomAWS",
    "Custom AWS",
    "CustomAws",
)


@dataclass
class Context:
    region: str
    domain_id: str
    domain_name: str
    root_domain_unit_id: str
    project_profile_name: str
    outputs: dict[str, Any]
    dry_run: bool
    custom_blueprint_id: str = ""


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
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


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
    outputs = load_tf_outputs()
    return Context(
        region=region,
        domain_id=domain_id,
        domain_name=str(domain["name"]),
        root_domain_unit_id=str(domain["rootDomainUnitId"]),
        project_profile_name=args.project_profile_name,
        outputs=outputs,
        dry_run=bool(args.dry_run),
    )


def _existing_project_id_from_outputs(outputs: dict[str, Any], slot_name: str) -> str:
    raw_slots = outputs.get("datazone_slot_project_ids")
    if isinstance(raw_slots, dict):
        value = raw_slots.get(slot_name)
        if isinstance(value, str) and value:
            return value
    raw_legacy = outputs.get(f"datazone_{slot_name}_project_id")
    if isinstance(raw_legacy, str) and raw_legacy:
        return raw_legacy
    return ""


def _build_datazone_slots_json(
    project_ids: dict[str, str],
    environment_ids: dict[str, str],
) -> str:
    slots = {
        project.slot: {
            "project_id": project_ids.get(project.slot, ""),
            "project_label": project.display_name,
            "environment_id": environment_ids.get(project.slot, ""),
        }
        for project in SEED_CONFIG.projects
    }
    return json.dumps(slots)


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
    by_id = {str(project["id"]): project for project in projects}
    ensured: dict[str, str] = {}

    for key, spec in PROJECT_SPECS.items():
        existing = by_name.get(spec["name"])
        if existing is None:
            existing_output_id = _existing_project_id_from_outputs(ctx.outputs, key)
            candidate = by_id.get(existing_output_id) if existing_output_id else None
            if candidate is not None and str(candidate.get("name") or "") == spec["name"]:
                existing = candidate
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


def _supports_environment_api(client: Any, ctx: Context, project_id: str) -> bool:
    try:
        client.list_environments(
            domainIdentifier=ctx.domain_id,
            projectIdentifier=project_id,
            maxResults=1,
        )
        return True
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ValidationException":
            print("Environment API: unsupported for this DataZone domain version")
            return False
        raise


def _find_custom_blueprint_id(client: Any, ctx: Context) -> str:
    try:
        blueprints = _list_all(
            client.list_environment_blueprints,
            "items",
            domainIdentifier=ctx.domain_id,
            managed=False,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "ValidationException":
            print("Custom blueprint lookup: unsupported for this DataZone domain version")
            return ""
        raise

    for candidate_name in CUSTOM_BLUEPRINT_CANDIDATE_NAMES:
        for blueprint in blueprints:
            if blueprint.get("name") == candidate_name:
                blueprint_id = str(blueprint["id"])
                print(f"Custom blueprint: {candidate_name} ({blueprint_id})")
                return blueprint_id

    print("Custom blueprint: not found")
    return ""


def _get_project_profile_id(client: Any, ctx: Context, project_id: str) -> str:
    try:
        resp = client.get_project(
            domainIdentifier=ctx.domain_id,
            identifier=project_id,
        )
        return str(resp.get("projectProfileId") or "")
    except ClientError:
        return ""


def _get_registry_env_config_id(client: Any, ctx: Context, profile_id: str) -> str:
    """Return the raja-registry environment configuration ID for a project profile."""
    try:
        resp = client.get_project_profile(
            domainIdentifier=ctx.domain_id,
            identifier=profile_id,
        )
    except ClientError:
        return ""
    for ec in resp.get("environmentConfigurations") or []:
        if ec.get("environmentBlueprintId") == ctx.custom_blueprint_id:
            return str(ec.get("id") or "")
    return ""


def _ensure_environments(
    client: Any,
    ctx: Context,
    project_ids: dict[str, str],
    registry_bucket: str,
    test_bucket: str,
) -> None:
    """Create raja-registry environments for each project if not already present.

    Requires the project to use a profile that includes the raja-registry blueprint.
    Projects using the AWS-managed 'All capabilities' profile are skipped with a warning.
    """
    if not ctx.custom_blueprint_id:
        print("Environments: skipped (custom blueprint not found)")
        return

    for key, environment_name in ENVIRONMENT_SPECS.items():
        project_id = project_ids.get(key, "")
        if not project_id:
            continue

        # Check whether environment already exists
        items = _list_all(
            client.list_environments,
            "items",
            domainIdentifier=ctx.domain_id,
            projectIdentifier=project_id,
        )
        existing = next(
            (i for i in items if str(i.get("name") or "") == environment_name),
            None,
        )
        if existing:
            env_id = str(existing.get("id") or "")
            env_status = str(existing.get("status") or "")
            print(f"Environment {key}: {environment_name} ({env_id}) [{env_status}]")
            continue

        # Determine the environment configuration ID from the project's profile
        project_profile_id = _get_project_profile_id(client, ctx, project_id)
        env_config_id = (
            _get_registry_env_config_id(client, ctx, project_profile_id)
            if project_profile_id
            else ""
        )
        if not env_config_id:
            print(
                f"Environment {key}: {environment_name} (skipped — project profile "
                f"{project_profile_id!r} has no raja-registry env config; "
                "re-create project with raja-default-profile to enable)"
            )
            continue

        print(f"Environment {key}: {environment_name} (missing)")
        if ctx.dry_run:
            print("  [DRY-RUN] Would create environment")
            continue

        try:
            created = client.create_environment(
                domainIdentifier=ctx.domain_id,
                projectIdentifier=project_id,
                name=environment_name,
                environmentConfigurationId=env_config_id,
                userParameters=[
                    {"name": "RegistryBucketName", "value": registry_bucket},
                    {"name": "TestBucketName", "value": test_bucket},
                ],
            )
            env_id = str(created.get("id") or "")
            print(f"  Created environment {env_id}")
        except ClientError as exc:
            print(f"  WARNING: failed to create {environment_name}: {exc}")


def _discover_environment_ids(
    client: Any,
    ctx: Context,
    project_ids: dict[str, str],
) -> dict[str, str]:
    discovered: dict[str, str] = {}

    for key, environment_name in ENVIRONMENT_SPECS.items():
        project_id = project_ids.get(key, "")
        if not project_id:
            continue
        if not _supports_environment_api(client, ctx, project_id):
            return {}
        items = _list_all(
            client.list_environments,
            "items",
            domainIdentifier=ctx.domain_id,
            projectIdentifier=project_id,
        )
        by_name = {str(item.get("name") or ""): item for item in items}
        item = by_name.get(environment_name)
        if not item:
            print(f"Environment {key}: {environment_name} (missing)")
            continue
        environment_project_id = str(item.get("projectId") or "")
        environment_id = str(item.get("id") or "")
        if environment_project_id and project_id != environment_project_id:
            print(
                f"Environment {key}: {environment_name} ({environment_id}) "
                f"belongs to unexpected project {environment_project_id}"
            )
            continue
        discovered[key] = environment_id
        print(f"Environment {key}: {environment_name} ({environment_id})")

    return discovered


def _lambda_name_from_arn(arn: str) -> str:
    return arn.rsplit(":", 1)[-1]


def _wait_for_lambda_update(lambda_client: Any, function_name: str) -> None:
    deadline = time.time() + 120
    while time.time() < deadline:
        response = lambda_client.get_function_configuration(FunctionName=function_name)
        status = str(response.get("LastUpdateStatus") or "")
        if status in {"", "Successful"}:
            return
        if status == "Failed":
            reason = response.get("LastUpdateStatusReason", "unknown reason")
            raise RuntimeError(f"lambda update failed for {function_name}: {reason}")
        time.sleep(2)
    raise RuntimeError(f"timed out waiting for lambda update: {function_name}")


def _sync_lambda_environment_ids(
    ctx: Context,
    project_ids: dict[str, str],
    environment_ids: dict[str, str],
) -> None:
    lambda_arns = [
        str(ctx.outputs.get("control_plane_lambda_arn") or ""),
        str(ctx.outputs.get("rale_authorizer_arn") or ""),
    ]
    lambda_names = [_lambda_name_from_arn(arn) for arn in lambda_arns if arn]
    if not lambda_names:
        print("Lambda environment sync: skipped (lambda outputs missing)")
        return

    env_updates = {
        "DATAZONE_DOMAIN_ID": ctx.domain_id,
        "DATAZONE_SLOTS": _build_datazone_slots_json(project_ids, environment_ids),
    }
    lambda_client = boto3.client("lambda", region_name=ctx.region)

    for function_name in lambda_names:
        configuration = lambda_client.get_function_configuration(FunctionName=function_name)
        current = dict(configuration.get("Environment", {}).get("Variables", {}))
        if all(current.get(key, "") == value for key, value in env_updates.items()):
            print(f"Lambda environment sync: {function_name} already current")
            continue
        merged = {**current, **env_updates}
        if ctx.dry_run:
            print(f"Lambda environment sync: [DRY-RUN] Would update {function_name}")
            continue
        lambda_client.update_function_configuration(
            FunctionName=function_name,
            Environment={"Variables": merged},
        )
        _wait_for_lambda_update(lambda_client, function_name)
        print(f"Lambda environment sync: updated {function_name}")


def _update_outputs(
    project_ids: dict[str, str],
    environment_ids: dict[str, str],
    ctx: Context,
) -> None:
    outputs = dict(ctx.outputs)
    updates = {
        "datazone_domain_id": ctx.domain_id,
        "datazone_slots": _build_datazone_slots_json(project_ids, environment_ids),
        "datazone_slot_project_ids": project_ids,
        "datazone_slot_environment_ids": environment_ids,
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


def _search_owner_listings(
    client: Any, ctx: Context, owner_project_id: str
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"domainIdentifier": ctx.domain_id, "maxResults": 50}
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.search_listings(**kwargs)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code == "ValidationException":
                print("Subscription grants: listing search unsupported for this domain version")
                return []
            raise
        for item in response.get("items", []):
            listing = item.get("assetListing")
            if isinstance(listing, dict) and listing.get("owningProjectId") == owner_project_id:
                items.append(listing)
        next_token = response.get("nextToken")
        if not next_token:
            return items


def _find_sub_request(
    client: Any,
    ctx: Context,
    listing_id: str,
    project_id: str,
    status: str,
) -> dict[str, Any] | None:
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "domainIdentifier": ctx.domain_id,
            "status": status,
            "maxResults": 50,
            "subscribedListingId": listing_id,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        response = client.list_subscription_requests(**kwargs)
        for item in response.get("items", []):
            if not isinstance(item, dict):
                continue
            principals = item.get("subscribedPrincipals", [])
            listings = item.get("subscribedListings", [])
            project_match = any(
                isinstance(p.get("project"), dict) and p["project"].get("id") == project_id
                for p in principals
            )
            listing_match = any(lst.get("id") == listing_id for lst in listings)
            if project_match and listing_match:
                return item
        next_token = response.get("nextToken")
        if not next_token:
            return None


def _ensure_subscription_grant(
    client: Any,
    ctx: Context,
    listing_id: str,
    listing_name: str,
    project_id: str,
    project_key: str,
) -> None:
    label = f"Subscription grant {project_key}/{listing_name}"
    if _find_sub_request(client, ctx, listing_id, project_id, "ACCEPTED"):
        print(f"{label}: present")
        return
    print(f"{label}: missing")
    if ctx.dry_run:
        print(f"  [DRY-RUN] Would create and accept subscription for {project_key}")
        return
    pending = _find_sub_request(client, ctx, listing_id, project_id, "PENDING")
    if pending is None:
        pending = client.create_subscription_request(
            clientToken=str(uuid.uuid4()),
            domainIdentifier=ctx.domain_id,
            requestReason=f"RAJA default grant for {listing_name}",
            subscribedListings=[{"identifier": listing_id}],
            subscribedPrincipals=[{"project": {"identifier": project_id}}],
        )
        print(f"  Created subscription request {pending['id']}")
    client.accept_subscription_request(
        domainIdentifier=ctx.domain_id,
        identifier=str(pending["id"]),
        decisionComment="Auto-approved by sagemaker_gaps.py",
    )
    print(f"  Accepted subscription grant for {project_key}")


def _ensure_default_subscription_grants(
    client: Any, ctx: Context, project_ids: dict[str, str]
) -> None:
    del client, ctx, project_ids
    print("Subscription bootstrap: skipped (seed_packages.py manages package grants)")


def _print_import_hints(ctx: Context, project_ids: dict[str, str]) -> None:
    print("\nTerraform import hints:")
    for key, project_id in project_ids.items():
        print(f"  terraform import aws_datazone_project.{key} {ctx.domain_id}:{project_id}")


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
        _ensure_default_subscription_grants(client, ctx, project_ids)
        ctx.custom_blueprint_id = _find_custom_blueprint_id(client, ctx)
        registry_bucket = str(ctx.outputs.get("rajee_registry_bucket_name") or "")
        test_bucket = str(ctx.outputs.get("rajee_test_bucket_name") or "")
        _ensure_environments(client, ctx, project_ids, registry_bucket, test_bucket)
        environment_ids = _discover_environment_ids(client, ctx, project_ids)
        _sync_lambda_environment_ids(ctx, project_ids, environment_ids)
        _update_outputs(project_ids, environment_ids, ctx)
        _print_import_hints(ctx, project_ids)
    except ClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
