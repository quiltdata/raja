#!/usr/bin/env python3
"""POC for DataZone-managed Glue import on the LF-native package_tag table."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.seed_glue_tables import (  # noqa: E402
    _asset_search,
    _ensure_glue_data_source,
    _ensure_subscription,
    _find_glue_data_source,
    _get_asset,
    _list_subscription_grants,
)
from scripts.tf_outputs import load_tf_outputs  # noqa: E402


class PocError(RuntimeError):
    """Raised when the package_tag import POC cannot proceed."""


@dataclass(frozen=True)
class PocContext:
    region: str
    account_id: str
    domain_id: str
    database_name: str
    owner_project_id: str
    subscriber_project_id: str
    owner_environment_id: str
    subscriber_environment_id: str
    table_name: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--table-name", default="package_tag", help="Glue table to probe.")
    parser.add_argument(
        "--subscriber-key",
        default="bio",
        help="Logical subscriber project key from tf outputs (default: bio).",
    )
    parser.add_argument(
        "--grant-publisher-role",
        action="store_true",
        help="Grant LF access to the owner Glue data source role before rerunning import.",
    )
    parser.add_argument(
        "--restart-import",
        action="store_true",
        help="Start a fresh DataZone Glue data source run.",
    )
    return parser.parse_args()


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


def _form_names(asset_detail: dict[str, Any]) -> list[str]:
    forms = asset_detail.get("formsOutput") or []
    if not isinstance(forms, list):
        return []
    names: list[str] = []
    for form in forms:
        if not isinstance(form, dict):
            continue
        name = str(form.get("formName") or "")
        if name:
            names.append(name)
    return names


def _asset_candidate_summary(asset_detail: dict[str, Any]) -> dict[str, Any]:
    listing = asset_detail.get("listing") or {}
    return {
        "asset_id": str(asset_detail.get("id") or ""),
        "name": str(asset_detail.get("name") or ""),
        "created_by": str(asset_detail.get("createdBy") or ""),
        "created_at": str(asset_detail.get("createdAt") or ""),
        "external_identifier": str(asset_detail.get("externalIdentifier") or ""),
        "listing_id": str(listing.get("listingId") or ""),
        "listing_status": str(listing.get("listingStatus") or ""),
        "form_names": _form_names(asset_detail),
    }


def _score_asset_as_imported_candidate(
    asset_detail: dict[str, Any], *, known_asset_ids: set[str]
) -> tuple[int, list[str]]:
    score = 0
    reasons: list[str] = []
    asset_id = str(asset_detail.get("id") or "")
    forms = set(_form_names(asset_detail))
    created_by = str(asset_detail.get("createdBy") or "")

    if asset_id and asset_id not in known_asset_ids:
        score += 10
        reasons.append("new asset id after import run")
    if "DataSourceReferenceForm" in forms:
        score += 8
        reasons.append("has DataSourceReferenceForm")
    if created_by == "SYSTEM":
        score += 4
        reasons.append("created by SYSTEM")
    if len(forms) > 1:
        score += 2
        reasons.append("has multiple forms")
    return score, reasons


def _get_context(args: argparse.Namespace) -> PocContext:
    outputs = load_tf_outputs()
    project_ids = outputs.get("datazone_project_ids") or {}
    environment_ids = outputs.get("datazone_project_environment_ids") or {}
    if not isinstance(project_ids, dict) or not isinstance(environment_ids, dict):
        raise PocError(
            "tf outputs are missing datazone_project_ids "
            "or datazone_project_environment_ids"
        )

    account_id = str(boto3.client("sts").get_caller_identity()["Account"])
    region = "us-east-1"
    domain_id = str(outputs.get("datazone_domain_id") or "")
    database_name = str(outputs.get("iceberg_lf_database_name") or "")
    owner_project_id = str(project_ids.get("alpha") or "")
    subscriber_project_id = str(project_ids.get(args.subscriber_key) or "")
    owner_environment_id = str(environment_ids.get("alpha") or "")
    subscriber_environment_id = str(environment_ids.get(args.subscriber_key) or "")

    if not domain_id or not database_name:
        raise PocError("tf outputs are missing datazone_domain_id or iceberg_lf_database_name")
    if not owner_project_id or not subscriber_project_id:
        raise PocError("tf outputs are missing owner or subscriber project IDs")
    if not owner_environment_id or not subscriber_environment_id:
        raise PocError("tf outputs are missing owner or subscriber environment IDs")

    return PocContext(
        region=region,
        account_id=account_id,
        domain_id=domain_id,
        database_name=database_name,
        owner_project_id=owner_project_id,
        subscriber_project_id=subscriber_project_id,
        owner_environment_id=owner_environment_id,
        subscriber_environment_id=subscriber_environment_id,
        table_name=args.table_name,
    )


def _get_environment_role_arn(client: Any, *, domain_id: str, environment_id: str) -> str:
    response = client.get_environment(domainIdentifier=domain_id, identifier=environment_id)
    role_arn = _extract_provisioned_value(response, "userRoleArn")
    if not role_arn:
        raise PocError(f"environment {environment_id} is missing userRoleArn")
    return role_arn


def _get_data_source_role_arn(client: Any, *, domain_id: str, data_source_id: str) -> str:
    response = client.get_data_source(domainIdentifier=domain_id, identifier=data_source_id)
    config = response.get("configuration") or {}
    glue_config = config.get("glueRunConfiguration") or {}
    role_arn = str(glue_config.get("dataAccessRole") or "")
    if not role_arn:
        raise PocError(
            f"data source {data_source_id} is missing "
            "glueRunConfiguration.dataAccessRole"
        )
    return role_arn


def _get_glue_table(glue_client: Any, *, database_name: str, table_name: str) -> dict[str, Any]:
    try:
        return glue_client.get_table(DatabaseName=database_name, Name=table_name)["Table"]
    except (ClientError, BotoCoreError) as exc:
        raise PocError(f"failed to read Glue table {database_name}.{table_name}") from exc


def _s3_uri_to_arn(location: str) -> str:
    if not location.startswith("s3://"):
        raise PocError(f"expected s3:// location, got {location!r}")
    bucket_and_key = location.removeprefix("s3://").rstrip("/")
    bucket, _, key = bucket_and_key.partition("/")
    if not bucket:
        raise PocError(f"could not parse S3 bucket from {location!r}")
    if not key:
        return f"arn:aws:s3:::{bucket}"
    return f"arn:aws:s3:::{bucket}/{key}"


def _grant_permission(
    lf_client: Any,
    *,
    principal_arn: str,
    resource: dict[str, Any],
    permissions: list[str],
    grant_permissions: list[str],
) -> None:
    try:
        lf_client.grant_permissions(
            Principal={"DataLakePrincipalIdentifier": principal_arn},
            Resource=resource,
            Permissions=permissions,
            PermissionsWithGrantOption=grant_permissions,
        )
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        message = str(exc)
        if code == "AlreadyExistsException" or "Permissions modification is invalid" in message:
            return
        raise


def _ensure_publisher_lf_permissions(
    *,
    lf_client: Any,
    principal_arn: str,
    database_name: str,
    table_name: str,
    table_location: str,
) -> None:
    data_location_arn = _s3_uri_to_arn(table_location)
    _grant_permission(
        lf_client,
        principal_arn=principal_arn,
        resource={"Database": {"Name": database_name}},
        permissions=["ALL"],
        grant_permissions=["ALL"],
    )
    _grant_permission(
        lf_client,
        principal_arn=principal_arn,
        resource={"Table": {"DatabaseName": database_name, "Name": table_name}},
        permissions=["ALL"],
        grant_permissions=["ALL"],
    )
    _grant_permission(
        lf_client,
        principal_arn=principal_arn,
        resource={"DataLocation": {"ResourceArn": data_location_arn}},
        permissions=["DATA_LOCATION_ACCESS"],
        grant_permissions=["DATA_LOCATION_ACCESS"],
    )


def _start_import_run(client: Any, *, domain_id: str, data_source_id: str) -> str:
    before = client.get_data_source(domainIdentifier=domain_id, identifier=data_source_id)
    last_run_at = str(before.get("lastRunAt") or "")
    client.start_data_source_run(
        domainIdentifier=domain_id,
        dataSourceIdentifier=data_source_id,
    )
    return last_run_at


def _wait_for_import_run(
    client: Any,
    *,
    domain_id: str,
    data_source_id: str,
    previous_last_run_at: str,
    timeout_seconds: int = 300,
) -> dict[str, Any]:
    deadline = time.time() + timeout_seconds
    observed_new_run = False
    while time.time() < deadline:
        response = client.get_data_source(domainIdentifier=domain_id, identifier=data_source_id)
        current_last_run_at = str(response.get("lastRunAt") or "")
        current_last_run_status = str(response.get("lastRunStatus") or "")
        if current_last_run_at and current_last_run_at != previous_last_run_at:
            observed_new_run = True
        if observed_new_run and current_last_run_status in {"SUCCESS", "FAILED"}:
            return response
        time.sleep(5)
    raise PocError(f"timed out waiting for data source run {data_source_id}")


def _find_matching_assets(
    client: Any,
    *,
    domain_id: str,
    owner_project_id: str,
    table_name: str,
    database_name: str,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    for item in _asset_search(client, domain_id, owner_project_id, table_name):
        if str(item.get("name") or "") != table_name:
            continue
        asset_id = str(item.get("identifier") or "")
        if not asset_id:
            continue
        detail = _get_asset(client, domain_id, asset_id)
        if str(detail.get("typeIdentifier") or "") != "amazon.datazone.GlueTableAssetType":
            continue
        external_identifier = str(detail.get("externalIdentifier") or "")
        if database_name not in external_identifier and table_name not in external_identifier:
            continue
        matches.append(detail)
    return matches


def _choose_imported_asset(
    assets: list[dict[str, Any]], *, known_asset_ids: set[str]
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    scored: list[tuple[int, dict[str, Any], list[str]]] = []
    for asset in assets:
        score, reasons = _score_asset_as_imported_candidate(asset, known_asset_ids=known_asset_ids)
        scored.append((score, asset, reasons))

    scored.sort(key=lambda item: item[0], reverse=True)
    summaries = []
    for score, asset, reasons in scored:
        summary = _asset_candidate_summary(asset)
        summary["import_score"] = score
        summary["import_reasons"] = reasons
        summaries.append(summary)

    if not scored or scored[0][0] <= 0:
        return None, summaries
    return scored[0][1], summaries


def _listing_id_for_asset(asset_detail: dict[str, Any]) -> str:
    listing = asset_detail.get("listing") or {}
    return str(listing.get("listingId") or "")


def _lf_permissions_for_table(
    lf_client: Any, *, database_name: str, table_name: str
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    resource = {"Table": {"DatabaseName": database_name, "Name": table_name}}
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Resource": resource}
        if next_token:
            kwargs["NextToken"] = next_token
        page = lf_client.list_permissions(**kwargs)
        page_items = page.get("PrincipalResourcePermissions") or []
        if isinstance(page_items, list):
            items.extend(page_items)
        next_token = page.get("NextToken")
        if not next_token:
            return items
    return items


def main() -> None:
    args = _parse_args()
    ctx = _get_context(args)

    datazone = boto3.client("datazone", region_name=ctx.region)
    glue = boto3.client("glue", region_name=ctx.region)
    lf = boto3.client("lakeformation", region_name=ctx.region)

    owner_role_arn = _get_environment_role_arn(
        datazone, domain_id=ctx.domain_id, environment_id=ctx.owner_environment_id
    )
    subscriber_role_arn = _get_environment_role_arn(
        datazone, domain_id=ctx.domain_id, environment_id=ctx.subscriber_environment_id
    )

    _ensure_glue_data_source(
        datazone,
        domain_id=ctx.domain_id,
        owner_project_id=ctx.owner_project_id,
        database_name=ctx.database_name,
        dry_run=False,
    )
    data_source_name = f"{ctx.database_name}-datasource"
    data_source = _find_glue_data_source(
        datazone, ctx.domain_id, ctx.owner_project_id, data_source_name
    )
    if data_source is None:
        raise PocError(f"owner Glue data source {data_source_name!r} was not found")
    data_source_id = str(data_source.get("dataSourceId") or data_source.get("id") or "")
    if not data_source_id:
        raise PocError(f"data source {data_source_name!r} is missing an identifier")

    data_source_role_arn = _get_data_source_role_arn(
        datazone, domain_id=ctx.domain_id, data_source_id=data_source_id
    )
    glue_table = _get_glue_table(
        glue, database_name=ctx.database_name, table_name=ctx.table_name
    )
    table_location = str((glue_table.get("StorageDescriptor") or {}).get("Location") or "")
    if not table_location:
        raise PocError(f"Glue table {ctx.table_name} is missing StorageDescriptor.Location")

    before_assets = _find_matching_assets(
        datazone,
        domain_id=ctx.domain_id,
        owner_project_id=ctx.owner_project_id,
        table_name=ctx.table_name,
        database_name=ctx.database_name,
    )
    before_asset_ids = {str(asset.get("id") or "") for asset in before_assets}

    print("=" * 60)
    print("LF-native imported Glue asset POC")
    print("=" * 60)
    print(f"Table:                 {ctx.table_name}")
    print(f"Database:              {ctx.database_name}")
    print(f"Data source:           {data_source_name} ({data_source_id})")
    print(f"Owner env role:        {owner_role_arn}")
    print(f"Data source role:      {data_source_role_arn}")
    print(f"Subscriber env role:   {subscriber_role_arn}")
    print(f"Table location:        {table_location}")
    print(f"Existing matching assets before run: {len(before_assets)}")

    if args.grant_publisher_role:
        _ensure_publisher_lf_permissions(
            lf_client=lf,
            principal_arn=data_source_role_arn,
            database_name=ctx.database_name,
            table_name=ctx.table_name,
            table_location=table_location,
        )
        print("Publisher LF grants:   ensured for data source role")

    run_result = datazone.get_data_source(
        domainIdentifier=ctx.domain_id, identifier=data_source_id
    )
    if args.restart_import:
        previous_last_run_at = _start_import_run(
            datazone, domain_id=ctx.domain_id, data_source_id=data_source_id
        )
        print(
            "Import run:            started "
            f"(previous lastRunAt={previous_last_run_at or 'none'})"
        )
        run_result = _wait_for_import_run(
            datazone,
            domain_id=ctx.domain_id,
            data_source_id=data_source_id,
            previous_last_run_at=previous_last_run_at,
        )
        print(
            "Import run result:     "
            f"{run_result.get('lastRunStatus')} "
            f"assetCount={run_result.get('lastRunAssetCount')}"
        )
        if run_result.get("lastRunErrorMessage"):
            print(f"Import run error:      {json.dumps(run_result['lastRunErrorMessage'])}")

    after_assets = _find_matching_assets(
        datazone,
        domain_id=ctx.domain_id,
        owner_project_id=ctx.owner_project_id,
        table_name=ctx.table_name,
        database_name=ctx.database_name,
    )
    imported_asset, asset_summaries = _choose_imported_asset(
        after_assets, known_asset_ids=before_asset_ids
    )
    print("\nAsset candidates:")
    print(json.dumps(asset_summaries, indent=2))

    if imported_asset is None:
        print(
            "\nRESULT: No imported asset candidate was identified.\n"
            "If the data source run succeeded, DataZone may be suppressing import because a\n"
            "manual asset already exists for the same Glue table/external identifier."
        )
        return

    imported_listing_id = _listing_id_for_asset(imported_asset)
    if not imported_listing_id:
        raise PocError("imported asset candidate has no active listing")

    request_id = _ensure_subscription(
        datazone,
        domain_id=ctx.domain_id,
        listing=type(
            "Listing",
            (),
            {
                "listing_id": imported_listing_id,
                "listing_revision": "",
                "asset_id": str(imported_asset.get("id") or ""),
                "asset_revision": str(imported_asset.get("revision") or ""),
                "name": ctx.table_name,
                "owner_project_id": ctx.owner_project_id,
            },
        )(),
        project_id=ctx.subscriber_project_id,
        dry_run=False,
        force=False,
    )
    print(f"\nSubscription request:  {request_id}")

    time.sleep(10)
    grants = _list_subscription_grants(datazone, ctx.domain_id, imported_listing_id)
    table_permissions = _lf_permissions_for_table(
        lf, database_name=ctx.database_name, table_name=ctx.table_name
    )
    subscriber_entries = [
        item
        for item in table_permissions
        if str((item.get("Principal") or {}).get("DataLakePrincipalIdentifier") or "")
        == subscriber_role_arn
    ]

    print("\nDataZone grant objects:")
    print(json.dumps(grants, indent=2, default=str))
    print("\nSubscriber LF table permissions:")
    print(json.dumps(subscriber_entries, indent=2, default=str))

    if grants and subscriber_entries:
        print(
            "\nRESULT: Imported-asset path produced both DataZone grant objects and\n"
            "subscriber LF permissions. This strongly supports H1/H2 as the root cause."
        )
        return
    if grants and not subscriber_entries:
        print(
            "\nRESULT: DataZone created grant objects, but subscriber LF permissions are "
            "still missing.\n"
            "That points to downstream fulfillment failure after grant object creation."
        )
        return
    print(
        "\nRESULT: Even after rerunning the managed Glue import, no grant objects or "
        "subscriber LF\n"
        "permissions appeared for the imported candidate."
    )


if __name__ == "__main__":
    try:
        main()
    except PocError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
