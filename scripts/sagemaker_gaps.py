#!/usr/bin/env python3
"""Fill known SageMaker Unified Studio / DataZone V2 deployment gaps.

This script patches the current Terraform provider gaps for RAJA's V2 domain:

1. Ensure the default project profile exists and is ENABLED.
2. Ensure the owner/users/guests projects exist in the V2 domain.
3. Ensure the root domain unit grants CREATE_ASSET_TYPE to owner projects.
4. If the deployed domain supports DataZone environments, provision the RAJA
   custom-blueprint environment plumbing and environment IDs.
5. Refresh infra/tf-outputs.json with discovered project and environment IDs.

It is safe to rerun. Existing resources are reused.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError, ParamValidationError

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.tf_outputs import load_tf_outputs

OUTPUTS_PATH = REPO_ROOT / "infra" / "tf-outputs.json"
ENV_PATH = REPO_ROOT / ".env"

DEFAULT_PROFILE_NAME = "raja-default-profile"
DEFAULT_PROFILE_DESCRIPTION = "Default project profile for RAJA Terraform-managed V2 projects"
REGISTRY_ENVIRONMENT_PROFILE_NAME = "RAJA registry"
REGISTRY_ENVIRONMENT_PROFILE_DESCRIPTION = (
    "RAJA DataZone environment profile for the Quilt registry and test data buckets"
)
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
ENVIRONMENT_SPECS = {
    "owner": "raja-owner-env",
    "users": "raja-users-env",
    "guests": "raja-guests-env",
}
ENVIRONMENT_DESCRIPTIONS = {
    "owner": "Owner-tier DataZone environment for the Quilt registry and test bucket",
    "users": "Users-tier DataZone environment for the Quilt registry and test bucket",
    "guests": "Guests-tier DataZone environment for the Quilt registry and test bucket",
}
# Blueprint ID created via the SageMaker Unified Studio console for domain
# dzd-45tgjtqytva0rr. Console-only creation; ID is stable after creation.
RAJA_BLUEPRINT_ID = "4b1p5czd9uf9uv"

CUSTOM_BLUEPRINT_CANDIDATE_NAMES = (
    "CustomAWS",
    "Custom AWS",
    "CustomAws",
)
ENVIRONMENT_ROLE_OUTPUTS = {
    "owner": "datazone_owner_environment_role_arn",
    "users": "datazone_users_environment_role_arn",
    "guests": "datazone_guests_environment_role_arn",
}


@dataclass
class Context:
    account_id: str
    region: str
    domain_id: str
    domain_name: str
    root_domain_unit_id: str
    project_profile_name: str
    outputs: dict[str, Any]
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
    outputs = load_tf_outputs()
    return Context(
        account_id=str(boto3.client("sts", region_name=region).get_caller_identity()["Account"]),
        region=region,
        domain_id=domain_id,
        domain_name=str(domain["name"]),
        root_domain_unit_id=str(domain["rootDomainUnitId"]),
        project_profile_name=args.project_profile_name,
        outputs=outputs,
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


def _is_api_unsupported(exc: Exception) -> bool:
    if isinstance(exc, ParamValidationError):
        return False
    if not isinstance(exc, ClientError):
        return False
    if exc.response.get("Error", {}).get("Code", "") != "ValidationException":
        return False
    return "API not supported for domain version" in str(exc)


def _supports_environment_api(client: Any, ctx: Context, project_id: str) -> bool:
    try:
        client.list_environments(
            domainIdentifier=ctx.domain_id,
            projectIdentifier=project_id,
            maxResults=1,
        )
        return True
    except Exception as exc:
        if _is_api_unsupported(exc):
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


def _supports_environment_profile_api(client: Any, ctx: Context) -> bool:
    try:
        client.list_environment_profiles(domainIdentifier=ctx.domain_id, maxResults=1)
        return True
    except Exception as exc:
        if _is_api_unsupported(exc):
            print("Environment profile API: unsupported for this DataZone domain version")
            return False
        raise


def _output_str(ctx: Context, key: str) -> str:
    value = ctx.outputs.get(key)
    return value if isinstance(value, str) else ""


def _role_arn_for_tier(ctx: Context, tier: str) -> str:
    return _output_str(ctx, ENVIRONMENT_ROLE_OUTPUTS[tier])


def _list_environment_blueprint_configurations(client: Any, ctx: Context) -> list[dict[str, Any]]:
    return _list_all(
        client.list_environment_blueprint_configurations,
        "items",
        domainIdentifier=ctx.domain_id,
    )


def _ensure_custom_blueprint_configuration(client: Any, ctx: Context, blueprint_id: str) -> bool:
    if not blueprint_id:
        return False

    try:
        configurations = _list_environment_blueprint_configurations(client, ctx)
    except Exception as exc:
        if _is_api_unsupported(exc):
            print("Blueprint configuration API: unsupported for this DataZone domain version")
            return False
        raise

    for configuration in configurations:
        if str(configuration.get("environmentBlueprintId") or "") != blueprint_id:
            continue
        enabled_regions = [str(region) for region in configuration.get("enabledRegions", [])]
        if ctx.region in enabled_regions:
            print(f"Blueprint configuration: present ({blueprint_id})")
            return True
        if ctx.dry_run:
            print(
                f"Blueprint configuration: [DRY-RUN] Would enable region {ctx.region} "
                f"for {blueprint_id}"
            )
            return True
        client.put_environment_blueprint_configuration(
            domainIdentifier=ctx.domain_id,
            environmentBlueprintIdentifier=blueprint_id,
            enabledRegions=sorted(set(enabled_regions + [ctx.region])),
        )
        print(f"Blueprint configuration: enabled region {ctx.region} for {blueprint_id}")
        return True

    if ctx.dry_run:
        print(f"Blueprint configuration: [DRY-RUN] Would create config for {blueprint_id}")
        return True

    client.put_environment_blueprint_configuration(
        domainIdentifier=ctx.domain_id,
        environmentBlueprintIdentifier=blueprint_id,
        enabledRegions=[ctx.region],
    )
    print(f"Blueprint configuration: created for {blueprint_id}")
    return True


def _ensure_environment_profile(
    client: Any,
    ctx: Context,
    blueprint_id: str,
    project_ids: dict[str, str],
) -> str:
    if not blueprint_id:
        return ""
    if not _supports_environment_profile_api(client, ctx):
        return ""

    profiles = _list_all(client.list_environment_profiles, "items", domainIdentifier=ctx.domain_id)
    owner_project_id = project_ids.get("owner", "")
    for profile in profiles:
        if str(profile.get("name") or "") != REGISTRY_ENVIRONMENT_PROFILE_NAME:
            continue
        if owner_project_id and str(profile.get("projectId") or "") not in {"", owner_project_id}:
            continue
        profile_id = str(profile.get("id") or "")
        if profile_id:
            print(f"Environment profile: {REGISTRY_ENVIRONMENT_PROFILE_NAME} ({profile_id})")
            return profile_id

    print(f"Environment profile: {REGISTRY_ENVIRONMENT_PROFILE_NAME} (missing)")
    if ctx.dry_run:
        print("  [DRY-RUN] Would create environment profile")
        return "dry-run-environment-profile"

    created = client.create_environment_profile(
        awsAccountId=ctx.account_id,
        awsAccountRegion=ctx.region,
        description=REGISTRY_ENVIRONMENT_PROFILE_DESCRIPTION,
        domainIdentifier=ctx.domain_id,
        environmentBlueprintIdentifier=blueprint_id,
        name=REGISTRY_ENVIRONMENT_PROFILE_NAME,
        projectIdentifier=project_ids["owner"],
    )
    profile_id = str(created["id"])
    print(f"  Created environment profile {profile_id}")
    return profile_id


def _wait_for_environment(client: Any, ctx: Context, project_id: str, environment_id: str) -> dict[str, Any]:
    deadline = time.time() + 600
    while time.time() < deadline:
        response = client.get_environment(
            domainIdentifier=ctx.domain_id,
            identifier=environment_id,
        )
        status = str(response.get("status") or "")
        if status == "ACTIVE":
            return response
        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED", "VALIDATION_FAILED"}:
            raise RuntimeError(f"environment {environment_id} entered terminal status {status}")
        time.sleep(5)
    raise RuntimeError(
        f"timed out waiting for environment {environment_id} in project {project_id} to become active"
    )


def _ensure_environment(
    client: Any,
    ctx: Context,
    tier: str,
    project_id: str,
    blueprint_id: str,
) -> str:
    environment_name = ENVIRONMENT_SPECS[tier]
    role_arn = _role_arn_for_tier(ctx, tier)
    if not project_id or not blueprint_id or not role_arn:
        return ""

    items = _list_all(
        client.list_environments,
        "items",
        domainIdentifier=ctx.domain_id,
        projectIdentifier=project_id,
    )
    for item in items:
        if str(item.get("name") or "") != environment_name:
            continue
        environment_id = str(item.get("id") or "")
        if environment_id:
            print(f"Environment {tier}: {environment_name} ({environment_id})")
            if not ctx.dry_run:
                client.associate_environment_role(
                    domainIdentifier=ctx.domain_id,
                    environmentIdentifier=environment_id,
                    environmentRoleArn=role_arn,
                )
            return environment_id

    print(f"Environment {tier}: {environment_name} (missing)")
    if ctx.dry_run:
        print(f"  [DRY-RUN] Would create environment {environment_name}")
        return f"dry-run-{tier}-environment"

    created = client.create_environment(
        domainIdentifier=ctx.domain_id,
        environmentBlueprintIdentifier=blueprint_id,
        name=environment_name,
        description=ENVIRONMENT_DESCRIPTIONS[tier],
        projectIdentifier=project_id,
        userParameters=[
            {"name": "RegistryBucketName", "value": _output_str(ctx, "rajee_registry_bucket_name")},
            {"name": "TestBucketName", "value": _output_str(ctx, "rajee_test_bucket_name")},
        ],
    )
    environment_id = str(created["id"])
    client.associate_environment_role(
        domainIdentifier=ctx.domain_id,
        environmentIdentifier=environment_id,
        environmentRoleArn=role_arn,
    )
    _wait_for_environment(client, ctx, project_id, environment_id)
    print(f"  Created environment {environment_id}")
    return environment_id


def _ensure_environments(
    client: Any,
    ctx: Context,
    project_ids: dict[str, str],
) -> dict[str, str]:
    owner_project_id = project_ids.get("owner", "")
    if not owner_project_id or not _supports_environment_api(client, ctx, owner_project_id):
        return {}

    blueprint_id = RAJA_BLUEPRINT_ID
    if not _ensure_custom_blueprint_configuration(client, ctx, blueprint_id):
        return _discover_environment_ids(client, ctx, project_ids)

    ensured: dict[str, str] = {}
    for tier, project_id in project_ids.items():
        ensured[tier] = _ensure_environment(client, ctx, tier, project_id, blueprint_id)
    return ensured


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


def _sync_lambda_environment_ids(ctx: Context, environment_ids: dict[str, str]) -> None:
    if not environment_ids:
        return

    lambda_arns = [
        str(ctx.outputs.get("control_plane_lambda_arn") or ""),
        str(ctx.outputs.get("rale_authorizer_arn") or ""),
    ]
    lambda_names = [_lambda_name_from_arn(arn) for arn in lambda_arns if arn]
    if not lambda_names:
        print("Lambda environment sync: skipped (lambda outputs missing)")
        return

    env_updates = {
        "DATAZONE_OWNER_ENVIRONMENT_ID": environment_ids.get("owner", ""),
        "DATAZONE_USERS_ENVIRONMENT_ID": environment_ids.get("users", ""),
        "DATAZONE_GUESTS_ENVIRONMENT_ID": environment_ids.get("guests", ""),
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
        "datazone_owner_project_id": project_ids["owner"],
        "datazone_users_project_id": project_ids["users"],
        "datazone_guests_project_id": project_ids["guests"],
        "datazone_owner_environment_id": environment_ids.get("owner", ""),
        "datazone_users_environment_id": environment_ids.get("users", ""),
        "datazone_guests_environment_id": environment_ids.get("guests", ""),
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
        environment_ids = _ensure_environments(client, ctx, project_ids)
        _sync_lambda_environment_ids(ctx, environment_ids)
        _update_outputs(project_ids, environment_ids, ctx)
        _print_import_hints(ctx, project_ids)
    except ClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
