#!/usr/bin/env python3
"""Create a disposable DataZone subscriber project and Lakehouse environment."""

from __future__ import annotations

import argparse
import json
import time
from datetime import datetime
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

DEFAULT_DOMAIN_ID = "dzd-6w14ep5r5owwh3"
DEFAULT_PROFILE_NAME = "All capabilities"
FALLBACK_PROFILE_NAMES = ("All capabilities", "raja-default-profile")
LAKEHOUSE_BLUEPRINT_ID = "d6y5smpdi8x9lz"
LAKEHOUSE_ENVIRONMENT_NAME = "Lakehouse Database"


class ThrowawaySubscriberError(RuntimeError):
    """Raised when the throwaway subscriber cannot be created."""


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--domain-id", default=DEFAULT_DOMAIN_ID)
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile-name", default=DEFAULT_PROFILE_NAME)
    parser.add_argument("--name-prefix", default="raja-throwaway")
    parser.add_argument(
        "--description",
        default="Throwaway subscriber project for LF-native imported Glue grant debugging",
    )
    parser.add_argument("--timeout-seconds", type=int, default=900)
    return parser.parse_args()


def _list_all(method: Any, key: str, **kwargs: Any) -> list[dict[str, Any]]:
    paginator = method.__self__.get_paginator(method.__name__)
    items: list[dict[str, Any]] = []
    for page in paginator.paginate(**kwargs):
        page_items = page.get(key, [])
        if isinstance(page_items, list):
            items.extend(page_items)
    return items


def _extract_provisioned_value(environment: dict[str, Any], name: str) -> str:
    resources = environment.get("provisionedResources") or []
    if not isinstance(resources, list):
        return ""
    for resource in resources:
        if not isinstance(resource, dict):
            continue
        if str(resource.get("name") or "") == name:
            return str(resource.get("value") or "")
    return ""


def _timestamped_name(prefix: str) -> str:
    return f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}"


def _profile_env_config_ids(profile_detail: dict[str, Any]) -> set[str]:
    configs = profile_detail.get("environmentConfigurations") or []
    if not isinstance(configs, list):
        return set()
    return {
        str(env_config.get("environmentBlueprintId") or "")
        for env_config in configs
        if isinstance(env_config, dict)
    }


def _find_project_profile(
    client: Any, *, domain_id: str, profile_name: str
) -> tuple[str, dict[str, Any]]:
    profiles = _list_all(client.list_project_profiles, "items", domainIdentifier=domain_id)
    for profile in profiles:
        if str(profile.get("name") or "") == profile_name:
            profile_id = str(profile.get("id") or "")
            if not profile_id:
                break
            detail = client.get_project_profile(
                domainIdentifier=domain_id,
                identifier=profile_id,
            )
            return profile_id, detail
    raise ThrowawaySubscriberError(f"project profile {profile_name!r} was not found")


def _choose_project_profile(
    client: Any, *, domain_id: str, requested_profile_name: str
) -> tuple[str, str, dict[str, Any]]:
    candidates = [requested_profile_name]
    for fallback in FALLBACK_PROFILE_NAMES:
        if fallback not in candidates:
            candidates.append(fallback)

    first_found: tuple[str, str, dict[str, Any]] | None = None
    for candidate in candidates:
        try:
            profile_id, detail = _find_project_profile(
                client, domain_id=domain_id, profile_name=candidate
            )
        except ThrowawaySubscriberError:
            continue
        if first_found is None:
            first_found = (candidate, profile_id, detail)
        if LAKEHOUSE_BLUEPRINT_ID in _profile_env_config_ids(detail):
            return candidate, profile_id, detail

    if first_found is not None:
        return first_found
    raise ThrowawaySubscriberError("no usable project profile was found")


def _wait_for_project_ready(
    client: Any, *, domain_id: str, project_id: str, timeout_seconds: int
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        project = client.get_project(domainIdentifier=domain_id, identifier=project_id)
        status = str(project.get("projectStatus") or project.get("status") or "")
        if status in {"ACTIVE", "SUCCESS", ""}:
            return project
        time.sleep(5)
    raise ThrowawaySubscriberError(f"project {project_id} did not become ready")


def _lakehouse_env_config(
    profile: dict[str, Any], *, project_profile_id: str
) -> dict[str, Any]:
    for env_config in profile.get("environmentConfigurations") or []:
        if str(env_config.get("environmentBlueprintId") or "") == LAKEHOUSE_BLUEPRINT_ID:
            return env_config
    raise ThrowawaySubscriberError(
        f"project profile {project_profile_id} has no Lakehouse environment configuration"
    )


def _find_lakehouse_environment(
    client: Any, *, domain_id: str, project_id: str
) -> dict[str, Any] | None:
    environments = _list_all(
        client.list_environments,
        "items",
        domainIdentifier=domain_id,
        projectIdentifier=project_id,
    )
    for environment in environments:
        if str(environment.get("environmentBlueprintId") or "") == LAKEHOUSE_BLUEPRINT_ID:
            return environment
    for environment in environments:
        if str(environment.get("name") or "") == LAKEHOUSE_ENVIRONMENT_NAME:
            return environment
    return None


def _wait_for_environment_active(
    client: Any, *, domain_id: str, environment_id: str, timeout_seconds: int
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        environment = client.get_environment(
            domainIdentifier=domain_id,
            identifier=environment_id,
        )
        status = str(environment.get("status") or "")
        if status == "ACTIVE":
            return environment
        if status in {"CREATE_FAILED", "UPDATE_FAILED", "DELETE_FAILED"}:
            raise ThrowawaySubscriberError(
                f"environment {environment_id} entered terminal status {status}"
            )
        time.sleep(10)
    raise ThrowawaySubscriberError(f"environment {environment_id} did not become ACTIVE")


def _wait_for_auto_environment(
    client: Any, *, domain_id: str, project_id: str, timeout_seconds: int
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        environment = _find_lakehouse_environment(
            client, domain_id=domain_id, project_id=project_id
        )
        if environment is None:
            time.sleep(10)
            continue
        environment_id = str(environment.get("id") or "")
        if not environment_id:
            time.sleep(10)
            continue
        return _wait_for_environment_active(
            client,
            domain_id=domain_id,
            environment_id=environment_id,
            timeout_seconds=max(30, int(deadline - time.time())),
        )
    raise ThrowawaySubscriberError(
        f"project {project_id} did not auto-create a Lakehouse environment"
    )


def main() -> None:
    args = _parse_args()
    client = boto3.client("datazone", region_name=args.region)

    project_name = _timestamped_name(args.name_prefix)
    try:
        profile_name, profile_id, profile = _choose_project_profile(
            client, domain_id=args.domain_id, requested_profile_name=args.profile_name
        )
        created_project = client.create_project(
            domainIdentifier=args.domain_id,
            name=project_name,
            description=args.description,
            projectProfileId=profile_id,
        )
        project_id = str(created_project.get("id") or "")
        if not project_id:
            raise ThrowawaySubscriberError("create_project returned no project id")

        _wait_for_project_ready(
            client,
            domain_id=args.domain_id,
            project_id=project_id,
            timeout_seconds=args.timeout_seconds,
        )

        env_config = _lakehouse_env_config(profile, project_profile_id=profile_id)
        env_config_id = str(env_config.get("id") or "")
        deployment_mode = str(env_config.get("deploymentMode") or "")

        if deployment_mode == "ON_CREATE":
            environment = _wait_for_auto_environment(
                client,
                domain_id=args.domain_id,
                project_id=project_id,
                timeout_seconds=args.timeout_seconds,
            )
            environment_id = str(environment.get("id") or "")
        else:
            created_environment = client.create_environment(
                domainIdentifier=args.domain_id,
                projectIdentifier=project_id,
                name=LAKEHOUSE_ENVIRONMENT_NAME,
                environmentConfigurationId=env_config_id,
            )
            environment_id = str(created_environment.get("id") or "")
            if not environment_id:
                raise ThrowawaySubscriberError("create_environment returned no environment id")
            environment = _wait_for_environment_active(
                client,
                domain_id=args.domain_id,
                environment_id=environment_id,
                timeout_seconds=args.timeout_seconds,
            )
    except (ClientError, BotoCoreError) as exc:
        raise ThrowawaySubscriberError(str(exc)) from exc

    result = {
        "project_name": project_name,
        "project_id": project_id,
        "project_profile_name": profile_name,
        "project_profile_id": profile_id,
        "environment_configuration_id": env_config_id,
        "environment_deployment_mode": deployment_mode,
        "environment_name": LAKEHOUSE_ENVIRONMENT_NAME,
        "environment_id": environment_id,
        "environment_user_role_arn": _extract_provisioned_value(environment, "userRoleArn"),
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
