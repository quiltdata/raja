#!/usr/bin/env python3
"""Register LF-native Iceberg Glue tables as DataZone assets and subscriptions."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from scripts.seed_config import load_seed_state, write_seed_state
from scripts.tf_outputs import load_tf_outputs

GLUE_TABLE_ASSET_TYPE = "amazon.datazone.GlueTableAssetType"
GLUE_TABLE_FORM_NAME = "GlueTableForm"
GLUE_TABLE_FORM_TYPE = "amazon.datazone.GlueTableFormType"
ICEBERG_TABLE_NAMES = (
    "package_entry",
    "package_manifest",
    "package_revision",
    "package_tag",
)


class SeedGlueTablesError(RuntimeError):
    """Raised when Glue-table DataZone seeding cannot complete."""


@dataclass(frozen=True)
class GlueAssetTypeConfig:
    asset_type_name: str
    asset_type_revision: str
    form_name: str
    form_type_name: str
    form_type_revision: str


@dataclass(frozen=True)
class DataZoneListing:
    listing_id: str
    listing_revision: str
    asset_id: str
    asset_revision: str
    name: str
    owner_project_id: str


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Show planned actions only.")
    parser.add_argument(
        "--database-name",
        default="",
        help="Override the LF-native Glue database name instead of reading infra/tf-outputs.json.",
    )
    parser.add_argument(
        "--owner-project-id",
        default="",
        help="Override the owner DataZone project ID instead of reading infra/tf-outputs.json.",
    )
    parser.add_argument(
        "--subscriber-project-ids",
        default="",
        help=(
            "Comma-separated subscriber project IDs. "
            "Defaults to users and guests from infra/tf-outputs.json."
        ),
    )
    return parser.parse_args()


def _get_region() -> str:
    return os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"


def _get_domain_id(outputs: dict[str, Any]) -> str:
    domain_id = str(
        os.environ.get("DATAZONE_DOMAIN_ID") or outputs.get("datazone_domain_id") or ""
    ).strip()
    if not domain_id:
        raise SeedGlueTablesError(
            "missing DATAZONE_DOMAIN_ID and infra/tf-outputs.json:datazone_domain_id"
        )
    return domain_id


def _get_database_name(args: argparse.Namespace, outputs: dict[str, Any]) -> str:
    if args.database_name:
        return str(args.database_name).strip()
    return str(outputs.get("iceberg_lf_database_name") or "").strip()


def _get_owner_project_id(args: argparse.Namespace, outputs: dict[str, Any]) -> str:
    if args.owner_project_id:
        return str(args.owner_project_id).strip()
    owner_project_id = str(outputs.get("datazone_owner_project_id") or "").strip()
    if not owner_project_id:
        raise SeedGlueTablesError("missing owner project ID in infra/tf-outputs.json")
    return owner_project_id


def _get_subscriber_project_ids(args: argparse.Namespace, outputs: dict[str, Any]) -> list[str]:
    if args.subscriber_project_ids:
        return [value.strip() for value in args.subscriber_project_ids.split(",") if value.strip()]

    subscriber_project_ids: list[str] = []
    for key in ("datazone_users_project_id", "datazone_guests_project_id"):
        value = str(outputs.get(key) or "").strip()
        if value:
            subscriber_project_ids.append(value)
    return subscriber_project_ids


def _get_account_id() -> str:
    return str(boto3.client("sts").get_caller_identity()["Account"])


def _get_glue_asset_type_config(client: Any, domain_id: str) -> GlueAssetTypeConfig:
    try:
        response = client.get_asset_type(
            domainIdentifier=domain_id,
            identifier=GLUE_TABLE_ASSET_TYPE,
        )
    except (ClientError, BotoCoreError) as exc:
        raise SeedGlueTablesError("failed to resolve DataZone Glue table asset type") from exc

    asset_type_revision = str(response.get("revision") or "").strip()
    forms_output = response.get("formsOutput") or {}
    if not asset_type_revision or not isinstance(forms_output, dict):
        raise SeedGlueTablesError("Glue table asset type response was missing revision metadata")

    form_output = forms_output.get(GLUE_TABLE_FORM_NAME)
    if not isinstance(form_output, dict):
        raise SeedGlueTablesError("GlueTableForm is not available in this DataZone domain")

    form_type_name = str(form_output.get("typeName") or "").strip()
    form_type_revision = str(form_output.get("typeRevision") or "").strip()
    if not form_type_name or not form_type_revision:
        raise SeedGlueTablesError("GlueTableForm metadata is incomplete in this DataZone domain")

    return GlueAssetTypeConfig(
        asset_type_name=GLUE_TABLE_ASSET_TYPE,
        asset_type_revision=asset_type_revision,
        form_name=GLUE_TABLE_FORM_NAME,
        form_type_name=form_type_name,
        form_type_revision=form_type_revision,
    )


def _build_external_identifier(
    account_id: str, region: str, database_name: str, table_name: str
) -> str:
    return f"glue://{account_id}/{region}/{database_name}/{table_name}"


def _asset_search(
    client: Any,
    domain_id: str,
    owner_project_id: str,
    search_text: str,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "domainIdentifier": domain_id,
            "maxResults": 50,
            "owningProjectIdentifier": owner_project_id,
            "searchScope": "ASSET",
            "searchText": search_text,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.search(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise SeedGlueTablesError("failed to search DataZone assets") from exc
        for item in response.get("items", []):
            asset = item.get("assetItem")
            if isinstance(asset, dict):
                items.append(asset)
        next_token = response.get("nextToken")
        if not next_token:
            return items


def _get_asset(client: Any, domain_id: str, asset_id: str) -> dict[str, Any]:
    try:
        response = client.get_asset(domainIdentifier=domain_id, identifier=asset_id)
    except (ClientError, BotoCoreError) as exc:
        raise SeedGlueTablesError(f"failed to fetch DataZone asset {asset_id}") from exc
    if not isinstance(response, dict):
        raise SeedGlueTablesError(f"unexpected DataZone asset payload for {asset_id}")
    return response


def _find_listing(
    client: Any,
    domain_id: str,
    owner_project_id: str,
    asset_type_name: str,
    external_identifier: str,
    table_name: str,
) -> DataZoneListing | None:
    for asset in _asset_search(client, domain_id, owner_project_id, table_name):
        if str(asset.get("name") or "") != table_name:
            continue
        if str(asset.get("typeIdentifier") or "") != asset_type_name:
            continue
        if str(asset.get("owningProjectId") or "") != owner_project_id:
            continue
        asset_id = str(asset.get("identifier") or "")
        if not asset_id:
            continue
        asset_detail = _get_asset(client, domain_id, asset_id)
        if str(asset_detail.get("externalIdentifier") or "") != external_identifier:
            continue
        listing = asset_detail.get("listing") or {}
        return DataZoneListing(
            listing_id=str(listing.get("listingId") or ""),
            listing_revision="",
            asset_id=asset_id,
            asset_revision=str(asset_detail.get("revision") or ""),
            name=table_name,
            owner_project_id=owner_project_id,
        )
    return None


def _glue_table_form_content(glue_table: dict[str, Any], account_id: str, region: str) -> str:
    storage_descriptor = glue_table.get("StorageDescriptor") or {}
    columns = storage_descriptor.get("Columns") or []
    database_name = str(glue_table["DatabaseName"])
    table_name = str(glue_table["Name"])
    payload = {
        "catalogId": account_id,
        "region": region,
        "databaseName": database_name,
        "tableName": table_name,
        "tableArn": f"arn:aws:glue:{region}:{account_id}:table/{database_name}/{table_name}",
        "columns": [
            {
                "columnName": str(column.get("Name") or ""),
                "dataType": str(column.get("Type") or ""),
            }
            for column in columns
            if column.get("Name")
        ],
    }
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _ensure_listing(
    datazone_client: Any,
    glue_client: Any,
    *,
    domain_id: str,
    owner_project_id: str,
    asset_type: GlueAssetTypeConfig,
    account_id: str,
    region: str,
    database_name: str,
    table_name: str,
    dry_run: bool,
) -> DataZoneListing:
    external_identifier = _build_external_identifier(account_id, region, database_name, table_name)
    existing = _find_listing(
        datazone_client,
        domain_id,
        owner_project_id,
        asset_type.asset_type_name,
        external_identifier,
        table_name,
    )
    if existing is not None:
        if existing.listing_id:
            print(f"Listing {table_name}: present ({existing.listing_id})")
            return existing
        print(f"Listing {table_name}: asset exists, publishing listing")
        if dry_run:
            return DataZoneListing(
                listing_id=f"dry-run-{table_name}",
                listing_revision="1",
                asset_id=existing.asset_id,
                asset_revision=existing.asset_revision,
                name=table_name,
                owner_project_id=owner_project_id,
            )
        asset_id = existing.asset_id
        asset_revision = existing.asset_revision or "1"
    else:
        print(f"Listing {table_name}: missing")
        if dry_run:
            return DataZoneListing(
                listing_id=f"dry-run-{table_name}",
                listing_revision="1",
                asset_id=f"dry-run-asset-{table_name}",
                asset_revision="1",
                name=table_name,
                owner_project_id=owner_project_id,
            )

        try:
            glue_table = glue_client.get_table(DatabaseName=database_name, Name=table_name)["Table"]
        except (ClientError, BotoCoreError) as exc:
            raise SeedGlueTablesError(
                f"failed to read Glue table {database_name}.{table_name}"
            ) from exc

        form_content = _glue_table_form_content(glue_table, account_id, region)
        try:
            response = datazone_client.create_asset(
                clientToken=str(uuid.uuid4()),
                domainIdentifier=domain_id,
                owningProjectIdentifier=owner_project_id,
                name=table_name,
                externalIdentifier=external_identifier,
                typeIdentifier=asset_type.asset_type_name,
                typeRevision=asset_type.asset_type_revision,
                formsInput=[
                    {
                        "formName": asset_type.form_name,
                        "typeIdentifier": asset_type.form_type_name,
                        "typeRevision": asset_type.form_type_revision,
                        "content": form_content,
                    }
                ],
            )
        except (ClientError, BotoCoreError) as exc:
            raise SeedGlueTablesError(
                f"failed to create DataZone asset for {table_name}: {exc}"
            ) from exc

        asset_id = str(response["id"])
        asset_revision = str(response.get("revision") or "1")
    try:
        datazone_client.create_listing_change_set(
            clientToken=str(uuid.uuid4()),
            domainIdentifier=domain_id,
            entityIdentifier=asset_id,
            entityRevision=asset_revision,
            entityType="ASSET",
            action="PUBLISH",
        )
    except (ClientError, BotoCoreError) as exc:
        raise SeedGlueTablesError(
            f"failed to publish DataZone listing for {table_name}: {exc}"
        ) from exc

    deadline = time.time() + 60
    while time.time() < deadline:
        listing = _find_listing(
            datazone_client,
            domain_id,
            owner_project_id,
            asset_type.asset_type_name,
            external_identifier,
            table_name,
        )
        if listing is not None:
            print(f"  Created listing {listing.listing_id}")
            return listing
        time.sleep(2)
    raise SeedGlueTablesError(f"timed out waiting for DataZone listing for {table_name}")


def _find_subscription_request(
    client: Any,
    domain_id: str,
    listing_id: str,
    project_id: str,
    status: str,
) -> dict[str, Any] | None:
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "domainIdentifier": domain_id,
            "status": status,
            "maxResults": 50,
            "subscribedListingId": listing_id,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.list_subscription_requests(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise SeedGlueTablesError("failed to list DataZone subscription requests") from exc
        for item in response.get("items", []):
            if not isinstance(item, dict):
                continue
            principals = item.get("subscribedPrincipals", [])
            listings = item.get("subscribedListings", [])
            project_match = any(
                isinstance(principal.get("project"), dict)
                and principal["project"].get("id") == project_id
                for principal in principals
            )
            listing_match = any(listing.get("id") == listing_id for listing in listings)
            if project_match and listing_match:
                return item
        next_token = response.get("nextToken")
        if not next_token:
            return None


def _get_subscription_request_status(client: Any, domain_id: str, request_id: str) -> str:
    try:
        response = client.get_subscription_request_details(
            domainIdentifier=domain_id,
            identifier=request_id,
        )
    except (ClientError, BotoCoreError) as exc:
        raise SeedGlueTablesError(
            f"failed to fetch subscription request {request_id}"
        ) from exc
    return str(response.get("status") or "")


def _ensure_subscription(
    client: Any,
    *,
    domain_id: str,
    listing: DataZoneListing,
    project_id: str,
    dry_run: bool,
) -> str:
    label = f"Subscription {project_id}/{listing.name}"
    accepted = _find_subscription_request(
        client, domain_id, listing.listing_id, project_id, "ACCEPTED"
    )
    if accepted is not None:
        request_id = str(accepted.get("id") or "")
        print(f"{label}: present ({request_id})")
        return request_id

    print(f"{label}: missing")
    if dry_run:
        return f"dry-run-subscription-{project_id}-{listing.name}"

    pending = _find_subscription_request(
        client, domain_id, listing.listing_id, project_id, "PENDING"
    )
    if pending is None:
        try:
            pending = client.create_subscription_request(
                clientToken=str(uuid.uuid4()),
                domainIdentifier=domain_id,
                requestReason=f"RAJA Glue table grant for {listing.name}",
                subscribedListings=[{"identifier": listing.listing_id}],
                subscribedPrincipals=[{"project": {"identifier": project_id}}],
            )
        except (ClientError, BotoCoreError) as exc:
            raise SeedGlueTablesError(
                "failed to create subscription request for "
                f"project {project_id} and {listing.name}: {exc}"
            ) from exc
        print(f"  Created subscription request {pending['id']}")

    request_id = str(pending["id"])
    try:
        client.accept_subscription_request(
            domainIdentifier=domain_id,
            identifier=request_id,
            decisionComment="Auto-approved by seed_glue_tables.py",
        )
    except (ClientError, BotoCoreError) as exc:
        deadline = time.time() + 10
        while time.time() < deadline:
            if _get_subscription_request_status(client, domain_id, request_id) == "ACCEPTED":
                print(f"  Subscription request {request_id} is already accepted")
                return request_id
            time.sleep(1)
        raise SeedGlueTablesError(
            f"failed to accept subscription request {request_id} for {listing.name}: {exc}"
        ) from exc
    print(f"  Accepted subscription request {request_id}")
    return request_id


def main() -> None:
    args = _parse_args()
    outputs = load_tf_outputs()
    database_name = _get_database_name(args, outputs)
    if not database_name:
        print("Glue table seeding: skipped (iceberg_lf_database_name is empty)")
        return

    region = _get_region()
    domain_id = _get_domain_id(outputs)
    owner_project_id = _get_owner_project_id(args, outputs)
    subscriber_project_ids = [
        project_id
        for project_id in _get_subscriber_project_ids(args, outputs)
        if project_id != owner_project_id
    ]
    if not subscriber_project_ids:
        raise SeedGlueTablesError("no subscriber project IDs were configured")

    account_id = _get_account_id()
    datazone_client = boto3.client("datazone", region_name=region)
    glue_client = boto3.client("glue", region_name=region)
    asset_type = _get_glue_asset_type_config(datazone_client, domain_id)

    print("=" * 60)
    print("Seeding DataZone Glue table assets")
    print(f"Domain:           {domain_id}")
    print(f"Database:         {database_name}")
    print(f"Owner project:    {owner_project_id}")
    print(f"Subscribers:      {', '.join(subscriber_project_ids)}")
    print(f"Asset type:       {asset_type.asset_type_name}@{asset_type.asset_type_revision}")
    print(f"Glue table form:  {asset_type.form_type_name}@{asset_type.form_type_revision}")
    if args.dry_run:
        print("Mode:             DRY-RUN")
    print("=" * 60)

    state = load_seed_state()
    glue_state: dict[str, dict[str, Any]] = {}
    for table_name in ICEBERG_TABLE_NAMES:
        listing = _ensure_listing(
            datazone_client,
            glue_client,
            domain_id=domain_id,
            owner_project_id=owner_project_id,
            asset_type=asset_type,
            account_id=account_id,
            region=region,
            database_name=database_name,
            table_name=table_name,
            dry_run=args.dry_run,
        )
        subscription_ids: dict[str, str] = {}
        for project_id in subscriber_project_ids:
            subscription_ids[project_id] = _ensure_subscription(
                datazone_client,
                domain_id=domain_id,
                listing=listing,
                project_id=project_id,
                dry_run=args.dry_run,
            )
        glue_state[table_name] = {
            "asset_id": listing.asset_id,
            "asset_revision": listing.asset_revision,
            "database_name": database_name,
            "external_identifier": _build_external_identifier(
                account_id, region, database_name, table_name
            ),
            "listing_id": listing.listing_id,
            "listing_revision": listing.listing_revision,
            "subscription_ids": subscription_ids,
        }

    if args.dry_run:
        print("Glue table seeding: dry-run complete")
        return

    state["glue_tables"] = glue_state
    write_seed_state(state)
    print("Glue table seeding: complete")


if __name__ == "__main__":
    try:
        main()
    except SeedGlueTablesError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(1)
