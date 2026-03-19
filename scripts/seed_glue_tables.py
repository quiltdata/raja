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

from scripts.seed_config import load_seed_config, load_seed_state, write_seed_state
from scripts.tf_outputs import load_tf_outputs

SEED_CONFIG = load_seed_config()

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
        "--force",
        action="store_true",
        help=(
            "Revoke existing accepted subscriptions before re-creating them. "
            "Use this to re-trigger DataZone's LF grant machinery after infrastructure changes."
        ),
    )
    parser.add_argument(
        "--inspect",
        action="store_true",
        help=(
            "Inspect subscription grant state via DataZone API. "
            "Reads saved seed state and reports what grants DataZone believes it issued "
            "(GRANTED/PENDING/FAILED) vs the subscriptions that are ACCEPTED."
        ),
    )
    parser.add_argument(
        "--database-name",
        default="",
        help="Override the LF-native Glue database name instead of reading infra/tf-outputs.json.",
    )
    parser.add_argument(
        "--owner-project-id",
        default="",
        help="Override the default project ID instead of reading infra/tf-outputs.json.",
    )
    parser.add_argument(
        "--subscriber-project-ids",
        default="",
        help=(
            "Comma-separated subscriber project IDs. "
            "Defaults to non-default projects from infra/tf-outputs.json."
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
    project_ids: dict[str, Any] = outputs.get("datazone_project_ids") or {}
    owner_project_id = str(project_ids.get(SEED_CONFIG.default_project) or "").strip()
    if not owner_project_id:
        raise SeedGlueTablesError(
            f"missing project ID for default project {SEED_CONFIG.default_project!r}"
            " in infra/tf-outputs.json:datazone_project_ids"
        )
    return owner_project_id


def _get_subscriber_project_ids(args: argparse.Namespace, outputs: dict[str, Any]) -> list[str]:
    if args.subscriber_project_ids:
        return [value.strip() for value in args.subscriber_project_ids.split(",") if value.strip()]

    project_ids: dict[str, Any] = outputs.get("datazone_project_ids") or {}
    return [
        str(project_ids[project.key])
        for project in SEED_CONFIG.projects
        if project.key != SEED_CONFIG.default_project and project_ids.get(project.key)
    ]


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


def _find_active_subscription(
    client: Any,
    domain_id: str,
    listing_id: str,
    project_id: str,
) -> dict[str, Any] | None:
    """Return the APPROVED subscription (not request) for a listing+project, or None."""
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "domainIdentifier": domain_id,
            "subscribedListingId": listing_id,
            "status": "APPROVED",
            "maxResults": 50,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.list_subscriptions(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise SeedGlueTablesError(
                f"failed to list subscriptions for listing {listing_id}"
            ) from exc
        for item in response.get("items", []):
            subscriber = item.get("subscribedPrincipal") or {}
            project = subscriber.get("project") or {}
            if project.get("id") == project_id:
                return item
        next_token = response.get("nextToken")
        if not next_token:
            return None


def _revoke_subscription(
    client: Any,
    domain_id: str,
    subscription_id: str,
    table_name: str,
) -> None:
    try:
        client.revoke_subscription(
            domainIdentifier=domain_id,
            identifier=subscription_id,
        )
    except (ClientError, BotoCoreError) as exc:
        raise SeedGlueTablesError(
            f"failed to revoke subscription {subscription_id} for {table_name}: {exc}"
        ) from exc
    print(f"  Revoked subscription {subscription_id}")


def _ensure_subscription(
    client: Any,
    *,
    domain_id: str,
    listing: DataZoneListing,
    project_id: str,
    dry_run: bool,
    force: bool = False,
) -> str:
    label = f"Subscription {project_id}/{listing.name}"

    if force:
        active = _find_active_subscription(client, domain_id, listing.listing_id, project_id)
        if active is not None:
            subscription_id = str(active.get("id") or "")
            print(f"{label}: revoking ({subscription_id})")
            if dry_run:
                print(f"  [DRY-RUN] Would revoke subscription {subscription_id}")
            else:
                _revoke_subscription(client, domain_id, subscription_id, listing.name)

    accepted = _find_subscription_request(
        client, domain_id, listing.listing_id, project_id, "ACCEPTED"
    )
    if accepted is not None and not force:
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


def _list_subscription_grants(
    client: Any, domain_id: str, listing_id: str
) -> list[dict[str, Any]]:
    grants: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "domainIdentifier": domain_id,
            "subscribedListingId": listing_id,
            "maxResults": 50,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.list_subscription_grants(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            raise SeedGlueTablesError(
                f"failed to list subscription grants for listing {listing_id}"
            ) from exc
        grants.extend(response.get("items", []))
        next_token = response.get("nextToken")
        if not next_token:
            return grants


def _inspect_subscription_grants(
    client: Any,
    *,
    domain_id: str,
    state: dict[str, Any],
) -> None:
    """Report what DataZone believes it granted for each Glue table subscription.

    Probes H4: accepted subscriptions may not have triggered DataZone-managed LF
    grants. If no grant objects exist at all, DataZone never attempted to issue LF
    permissions. If grants exist but have status GRANT_FAILED, DataZone tried and
    failed (useful for H2 investigation — likely a missing grantable permission on
    the publisher side).
    """
    glue_state: dict[str, Any] = state.get("glue_tables") or {}
    if not glue_state:
        print("No Glue table seed state found — run without --inspect first.")
        return

    print("=" * 60)
    print("DataZone subscription grant inspection")
    print("=" * 60)

    any_missing = False
    for table_name, table_state in sorted(glue_state.items()):
        listing_id: str = str(table_state.get("listing_id") or "")
        subscription_ids: dict[str, str] = table_state.get("subscription_ids") or {}
        if not listing_id:
            print(f"\n{table_name}: no listing_id in seed state — skipping")
            continue

        print(f"\n{table_name} (listing {listing_id})")
        grants = _list_subscription_grants(client, domain_id, listing_id)
        if not grants:
            print("  ✗ No subscription grant objects found — DataZone never attempted LF grants")
            any_missing = True
            continue

        for grant in grants:
            grant_id = str(grant.get("id") or "")
            grant_status = str(grant.get("status") or "?")
            sub_id = str(grant.get("subscriptionId") or "")
            assets = grant.get("assets") or []
            print(f"  Grant {grant_id}  status={grant_status}  subscription={sub_id}")
            for asset in assets:
                asset_id = str(asset.get("assetId") or "")
                asset_status = str(asset.get("status") or "?")
                failure_cause = asset.get("failureCause") or {}
                failure_msg = str(failure_cause.get("message") or "")
                line = f"    asset={asset_id}  status={asset_status}"
                if failure_msg:
                    line += f"  failure={failure_msg}"
                print(line)

        # Cross-reference: warn about subscription request IDs that have no matching grant
        grant_sub_ids = {str(g.get("subscriptionId") or "") for g in grants}
        for project_id, request_id in sorted(subscription_ids.items()):
            if request_id not in grant_sub_ids:
                print(
                    f"  ✗ Subscription request {request_id} (project {project_id})"
                    " has no matching grant object"
                )
                any_missing = True

    print("\n" + "=" * 60)
    if any_missing:
        print(
            "RESULT: At least one accepted subscription has no grant object.\n"
            "DataZone never initiated LF grant fulfillment for those subscriptions.\n"
            "This confirms H4 and narrows root cause to H1 (data-source linkage) or\n"
            "H2 (publisher-side grantable permissions)."
        )
    else:
        print("RESULT: All subscriptions have grant objects — check statuses above for GRANT_FAILED.")


DEFAULT_LAKEHOUSE_CONNECTION_NAME = "project.default_lakehouse"


def _find_glue_data_source(
    client: Any,
    domain_id: str,
    project_id: str,
    data_source_name: str,
) -> dict[str, Any] | None:
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "domainIdentifier": domain_id,
            "projectIdentifier": project_id,
            "maxResults": 50,
            "type": "GLUE",
        }
        if next_token:
            kwargs["nextToken"] = next_token
        try:
            response = client.list_data_sources(**kwargs)
        except (ClientError, BotoCoreError) as exc:
            code = exc.response.get("Error", {}).get("Code", "") if isinstance(exc, ClientError) else ""
            if code in ("AccessDeniedException", "ValidationException"):
                print(f"Glue data source: list_data_sources unsupported ({code}) — skipping")
                return None
            raise
        for item in response.get("items", []):
            if str(item.get("name") or "") == data_source_name:
                return item
        next_token = response.get("nextToken")
        if not next_token:
            return None


def _find_project_connection_id(
    client: Any,
    domain_id: str,
    project_id: str,
    *,
    connection_name: str,
    connection_type: str | None = None,
) -> str:
    next_token: str | None = None
    while True:
        kwargs: dict[str, Any] = {
            "domainIdentifier": domain_id,
            "projectIdentifier": project_id,
            "maxResults": 50,
        }
        if next_token:
            kwargs["nextToken"] = next_token
        response = client.list_connections(**kwargs)
        for item in response.get("items", []):
            if str(item.get("name") or "") != connection_name:
                continue
            if connection_type and str(item.get("type") or "") != connection_type:
                continue
            connection_id = str(item.get("connectionId") or item.get("id") or "")
            if connection_id:
                return connection_id
        next_token = response.get("nextToken")
        if not next_token:
            return ""


def _wait_for_data_source_ready(client: Any, domain_id: str, data_source_id: str) -> bool:
    deadline = time.time() + 120
    while time.time() < deadline:
        response = client.get_data_source(
            domainIdentifier=domain_id,
            identifier=data_source_id,
        )
        status = str(response.get("status") or "")
        if status == "READY":
            return True
        if status in {"FAILED_CREATION", "FAILED_UPDATE", "FAILED_DELETION"}:
            print(f"  WARNING: data source {data_source_id} entered terminal status {status}")
            return False
        time.sleep(2)
    print(f"  WARNING: timed out waiting for data source {data_source_id} to become READY")
    return False


def _start_data_source_run_if_ready(client: Any, domain_id: str, data_source_id: str) -> None:
    if not _wait_for_data_source_ready(client, domain_id, data_source_id):
        return
    try:
        client.start_data_source_run(
            domainIdentifier=domain_id,
            dataSourceIdentifier=data_source_id,
        )
        print(
            f"  Started initial run for {data_source_id} — "
            "check DataZone console for import status"
        )
    except (ClientError, BotoCoreError) as exc:
        print(f"  WARNING: failed to start data source run: {exc}")


def _ensure_glue_data_source(
    client: Any,
    *,
    domain_id: str,
    owner_project_id: str,
    database_name: str,
    dry_run: bool,
) -> None:
    """Ensure a DataZone-managed Glue data source exists for the LF-native Iceberg database."""
    data_source_name = f"{database_name}-datasource"
    existing = _find_glue_data_source(client, domain_id, owner_project_id, data_source_name)
    if existing is not None:
        data_source_id = str(existing.get("dataSourceId") or existing.get("id") or "")
        data_source_status = str(existing.get("status") or "?")
        print(f"Glue data source: present ({data_source_id}) [{data_source_status}]")
        if data_source_id and int(existing.get("lastRunAssetCount") or 0) == 0:
            _start_data_source_run_if_ready(client, domain_id, data_source_id)
        return

    connection_id = _find_project_connection_id(
        client,
        domain_id,
        owner_project_id,
        connection_name=DEFAULT_LAKEHOUSE_CONNECTION_NAME,
        connection_type="LAKEHOUSE",
    )
    if not connection_id:
        print(
            f"Glue data source: skipped ({DEFAULT_LAKEHOUSE_CONNECTION_NAME!r} connection not found)"
        )
        return

    print(f"Glue data source: creating {data_source_name!r}")
    if dry_run:
        print("  [DRY-RUN] Would create Glue data source and start initial run")
        return

    try:
        response = client.create_data_source(
            clientToken=str(uuid.uuid4()),
            domainIdentifier=domain_id,
            projectIdentifier=owner_project_id,
            name=data_source_name,
            type="GLUE",
            connectionIdentifier=connection_id,
            publishOnImport=True,
            configuration={
                "glueRunConfiguration": {
                    "relationalFilterConfigurations": [
                        {
                            "databaseName": database_name,
                            "filterExpressions": [{"expression": "*", "type": "INCLUDE"}],
                        }
                    ]
                }
            },
        )
    except (ClientError, BotoCoreError) as exc:
        print(f"  WARNING: failed to create Glue data source: {exc}")
        return

    data_source_id = str(response.get("id") or "")
    print(f"  Created data source {data_source_id}")
    _start_data_source_run_if_ready(client, domain_id, data_source_id)


def main() -> None:
    args = _parse_args()
    outputs = load_tf_outputs()

    if args.inspect:
        region = _get_region()
        domain_id = _get_domain_id(outputs)
        state = load_seed_state()
        datazone_client = boto3.client("datazone", region_name=region)
        _inspect_subscription_grants(datazone_client, domain_id=domain_id, state=state)
        return

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

    _ensure_glue_data_source(
        datazone_client,
        domain_id=domain_id,
        owner_project_id=owner_project_id,
        database_name=database_name,
        dry_run=args.dry_run,
    )

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
    if args.force:
        print("Mode:             FORCE (revoke + re-subscribe)")
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
                force=args.force,
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
