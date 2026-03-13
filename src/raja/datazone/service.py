from __future__ import annotations

import hashlib
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from raja.quilt_uri import parse_quilt_uri

_PROJECT_PREFIX = "raja-principal"
_SEARCH_PAGE_SIZE = 50


class DataZoneError(RuntimeError):
    """Raised when DataZone package authorization state is unavailable."""


@dataclass(frozen=True)
class DataZoneConfig:
    domain_id: str
    owner_project_id: str = ""
    asset_type_name: str = "QuiltPackage"
    asset_type_revision: str = "1"

    @classmethod
    def from_env(cls) -> DataZoneConfig:
        domain_id = os.environ.get("DATAZONE_DOMAIN_ID", "").strip()
        if not domain_id:
            raise DataZoneError("DATAZONE_DOMAIN_ID is required")
        return cls(
            domain_id=domain_id,
            owner_project_id=os.environ.get("DATAZONE_OWNER_PROJECT_ID", "").strip(),
            asset_type_name=os.environ.get("DATAZONE_PACKAGE_ASSET_TYPE", "QuiltPackage").strip()
            or "QuiltPackage",
            asset_type_revision=os.environ.get("DATAZONE_PACKAGE_ASSET_TYPE_REVISION", "1").strip()
            or "1",
        )


@dataclass(frozen=True)
class DataZonePackageListing:
    listing_id: str
    listing_revision: str
    asset_id: str
    asset_revision: str
    name: str
    owner_project_id: str


def datazone_enabled() -> bool:
    return bool(os.environ.get("DATAZONE_DOMAIN_ID", "").strip())


def project_name_for_principal(principal: str) -> str:
    raw = principal.strip()
    if not raw:
        raise ValueError("principal is required")
    if '::"' in raw and raw.endswith('"'):
        raw = raw.rsplit('::"', 1)[1][:-1]
    elif "::" in raw:
        raw = raw.split("::", 1)[1].strip('"')
    slug = re.sub(r"[^a-z0-9-]+", "-", raw.lower()).strip("-") or "principal"
    digest = hashlib.sha1(principal.encode("utf-8")).hexdigest()[:10]
    base = f"{_PROJECT_PREFIX}-{slug}"
    if len(base) > 52:
        base = base[:52].rstrip("-")
    return f"{base}-{digest}"


class DataZoneService:
    def __init__(self, client: Any, config: DataZoneConfig) -> None:
        self._client = client
        self._config = config

    @property
    def domain_id(self) -> str:
        return self._config.domain_id

    def ensure_project_for_principal(self, principal: str) -> dict[str, str]:
        project_name = project_name_for_principal(principal)
        existing = self._find_project_by_name(project_name)
        if existing is not None:
            return {"project_id": existing["id"], "project_name": project_name}

        try:
            response = self._client.create_project(
                domainIdentifier=self._config.domain_id,
                name=project_name,
                description=f"RAJA principal mapping for {principal}",
            )
        except (ClientError, BotoCoreError) as exc:
            raise DataZoneError(f"failed to create DataZone project for {principal}") from exc
        return {"project_id": str(response["id"]), "project_name": project_name}

    def find_package_listing(self, quilt_uri: str) -> DataZonePackageListing | None:
        parsed = parse_quilt_uri(quilt_uri)
        items = self._search_listings(parsed.package_name)
        for item in items:
            if item.get("name") != parsed.package_name:
                continue
            if item.get("entityType") != self._config.asset_type_name:
                continue
            listing_id = str(item.get("listingId") or "")
            asset_id = str(item.get("entityId") or "")
            if not listing_id or not asset_id:
                continue
            return DataZonePackageListing(
                listing_id=listing_id,
                listing_revision=str(item.get("listingRevision") or ""),
                asset_id=asset_id,
                asset_revision=str(item.get("entityRevision") or ""),
                name=str(item.get("name") or parsed.package_name),
                owner_project_id=str(item.get("owningProjectId") or ""),
            )
        return None

    def has_package_grant(self, *, project_id: str, quilt_uri: str) -> bool:
        listing = self.find_package_listing(quilt_uri)
        if listing is None:
            return False
        return self._has_listing_grant(project_id=project_id, listing_id=listing.listing_id)

    def ensure_package_listing(self, quilt_uri: str) -> DataZonePackageListing:
        if not self._config.owner_project_id:
            raise DataZoneError("DATAZONE_OWNER_PROJECT_ID is required")

        existing = self.find_package_listing(quilt_uri)
        if existing is not None:
            return existing

        parsed = parse_quilt_uri(quilt_uri)
        try:
            response = self._client.create_asset(
                clientToken=str(uuid.uuid4()),
                domainIdentifier=self._config.domain_id,
                name=parsed.package_name,
                description=f"RAJA package asset for {parsed.package_name}",
                externalIdentifier=quilt_uri,
                typeIdentifier=self._config.asset_type_name,
                typeRevision=self._config.asset_type_revision,
                owningProjectIdentifier=self._config.owner_project_id,
                formsInput=[],
            )
        except (ClientError, BotoCoreError) as exc:
            raise DataZoneError(
                f"failed to create DataZone asset for {parsed.package_name}"
            ) from exc

        asset_id = str(response["id"])
        asset_revision = str(response.get("revision") or "1")
        try:
            self._client.create_listing_change_set(
                clientToken=str(uuid.uuid4()),
                domainIdentifier=self._config.domain_id,
                entityIdentifier=asset_id,
                entityRevision=asset_revision,
                entityType="ASSET",
                action="PUBLISH",
            )
        except (ClientError, BotoCoreError) as exc:
            raise DataZoneError(
                f"failed to publish DataZone listing for {parsed.package_name}"
            ) from exc

        deadline = time.time() + 60
        while time.time() < deadline:
            listing = self.find_package_listing(quilt_uri)
            if listing is not None:
                return listing
            time.sleep(2)
        raise DataZoneError(f"timed out waiting for DataZone listing for {parsed.package_name}")

    def ensure_project_package_grant(self, *, project_id: str, quilt_uri: str) -> str:
        listing = self.ensure_package_listing(quilt_uri)
        if self._has_listing_grant(project_id=project_id, listing_id=listing.listing_id):
            return listing.listing_id

        pending = self._find_subscription_request(
            project_id=project_id,
            listing_id=listing.listing_id,
            status="PENDING",
        )
        if pending is None:
            try:
                pending = self._client.create_subscription_request(
                    clientToken=str(uuid.uuid4()),
                    domainIdentifier=self._config.domain_id,
                    requestReason=f"RAJA access grant for {listing.name}",
                    subscribedListings=[{"identifier": listing.listing_id}],
                    subscribedPrincipals=[{"project": {"identifier": project_id}}],
                )
            except (ClientError, BotoCoreError) as exc:
                raise DataZoneError(
                    f"failed to create subscription request for project {project_id}"
                ) from exc

        request_id = str(pending["id"])
        try:
            self._client.accept_subscription_request(
                domainIdentifier=self._config.domain_id,
                identifier=request_id,
                decisionComment="Approved by RAJA POC bootstrap",
            )
        except (ClientError, BotoCoreError) as exc:
            if self._has_listing_grant(project_id=project_id, listing_id=listing.listing_id):
                return listing.listing_id
            raise DataZoneError(
                f"failed to accept subscription request {request_id} for project {project_id}"
            ) from exc
        return listing.listing_id

    def _search_listings(self, package_name: str) -> list[dict[str, Any]]:
        next_token: str | None = None
        matches: list[dict[str, Any]] = []
        while True:
            kwargs: dict[str, Any] = {
                "domainIdentifier": self._config.domain_id,
                "searchText": package_name,
                "maxResults": _SEARCH_PAGE_SIZE,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            try:
                response = self._client.search_listings(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                raise DataZoneError("failed to search DataZone listings") from exc
            for result in response.get("items", []):
                listing = result.get("assetListing")
                if isinstance(listing, dict):
                    matches.append(listing)
            next_token = response.get("nextToken")
            if not next_token:
                return matches

    def _find_project_by_name(self, project_name: str) -> dict[str, Any] | None:
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "domainIdentifier": self._config.domain_id,
                "name": project_name,
                "maxResults": _SEARCH_PAGE_SIZE,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            try:
                response = self._client.list_projects(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                raise DataZoneError("failed to list DataZone projects") from exc
            for item in response.get("items", []):
                if isinstance(item, dict) and item.get("name") == project_name:
                    return item
            next_token = response.get("nextToken")
            if not next_token:
                return None

    def _has_listing_grant(self, *, project_id: str, listing_id: str) -> bool:
        return (
            self._find_subscription_request(
                project_id=project_id,
                listing_id=listing_id,
                status="ACCEPTED",
            )
            is not None
        )

    def _find_subscription_request(
        self,
        *,
        project_id: str,
        listing_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "domainIdentifier": self._config.domain_id,
                "status": status,
                "maxResults": _SEARCH_PAGE_SIZE,
                "subscribedListingId": listing_id,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            try:
                response = self._client.list_subscription_requests(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                raise DataZoneError("failed to list DataZone subscription requests") from exc
            for item in response.get("items", []):
                if not isinstance(item, dict):
                    continue
                principals = item.get("subscribedPrincipals", [])
                listings = item.get("subscribedListings", [])
                if self._subscription_matches(
                    principals=principals,
                    listings=listings,
                    project_id=project_id,
                    listing_id=listing_id,
                ):
                    return item
            next_token = response.get("nextToken")
            if not next_token:
                return None

    @staticmethod
    def _subscription_matches(
        *,
        principals: list[dict[str, Any]],
        listings: list[dict[str, Any]],
        project_id: str,
        listing_id: str,
    ) -> bool:
        project_match = any(
            isinstance(principal.get("project"), dict)
            and principal["project"].get("id") == project_id
            for principal in principals
        )
        listing_match = any(listing.get("id") == listing_id for listing in listings)
        return project_match and listing_match
