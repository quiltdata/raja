from __future__ import annotations

import os
import time
import uuid
from dataclasses import dataclass
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from raja.quilt_uri import parse_quilt_uri

_SEARCH_PAGE_SIZE = 50


class DataZoneError(RuntimeError):
    """Raised when DataZone package authorization state is unavailable."""


@dataclass(frozen=True)
class DataZoneConfig:
    domain_id: str
    owner_project_id: str = ""
    users_project_id: str = ""
    guests_project_id: str = ""
    owner_project_label: str = "Project A"
    users_project_label: str = "Project B"
    guests_project_label: str = "Project C"
    owner_environment_id: str = ""
    users_environment_id: str = ""
    guests_environment_id: str = ""
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
            users_project_id=os.environ.get("DATAZONE_USERS_PROJECT_ID", "").strip(),
            guests_project_id=os.environ.get("DATAZONE_GUESTS_PROJECT_ID", "").strip(),
            owner_project_label=os.environ.get("DATAZONE_OWNER_PROJECT_LABEL", "Project A").strip()
            or "Project A",
            users_project_label=os.environ.get("DATAZONE_USERS_PROJECT_LABEL", "Project B").strip()
            or "Project B",
            guests_project_label=os.environ.get(
                "DATAZONE_GUESTS_PROJECT_LABEL", "Project C"
            ).strip()
            or "Project C",
            owner_environment_id=os.environ.get("DATAZONE_OWNER_ENVIRONMENT_ID", "").strip(),
            users_environment_id=os.environ.get("DATAZONE_USERS_ENVIRONMENT_ID", "").strip(),
            guests_environment_id=os.environ.get("DATAZONE_GUESTS_ENVIRONMENT_ID", "").strip(),
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


def project_id_for_scopes(scopes: list[str], config: DataZoneConfig) -> str:
    """Return the configured DataZone project ID for a principal based on scopes.

    Classification rules:
    - Any scope containing a wildcard (*) -> first configured project slot
    - Any scope with a non-read action -> second configured project slot
    - Otherwise -> third configured project slot
    """
    has_wildcard = any("*" in s for s in scopes)
    if has_wildcard:
        return config.owner_project_id

    has_write = any(not s.rsplit(":", 1)[-1].lower().startswith("read") for s in scopes if ":" in s)
    if has_write:
        return config.users_project_id

    return config.guests_project_id


class DataZoneService:
    def __init__(self, client: Any, config: DataZoneConfig) -> None:
        self._client = client
        self._config = config

    @property
    def domain_id(self) -> str:
        return self._config.domain_id

    def find_package_listing(
        self,
        quilt_uri: str,
        *,
        owner_project_id: str | None = None,
    ) -> DataZonePackageListing | None:
        parsed = parse_quilt_uri(quilt_uri)
        items = self._search_listings(parsed.package_name)
        for item in items:
            if item.get("name") != parsed.package_name:
                continue
            if item.get("entityType") != self._config.asset_type_name:
                continue
            listing_id = str(item.get("listingId") or "")
            asset_id = str(item.get("entityId") or "")
            candidate_owner_project_id = str(item.get("owningProjectId") or "")
            if not listing_id or not asset_id:
                continue
            if owner_project_id and candidate_owner_project_id != owner_project_id:
                continue
            external_identifier = self._get_asset_external_identifier(asset_id)
            if external_identifier and external_identifier != quilt_uri:
                continue
            return DataZonePackageListing(
                listing_id=listing_id,
                listing_revision=str(item.get("listingRevision") or ""),
                asset_id=asset_id,
                asset_revision=str(item.get("entityRevision") or ""),
                name=str(item.get("name") or parsed.package_name),
                owner_project_id=candidate_owner_project_id,
            )
        return None

    def list_package_listings(self) -> list[DataZonePackageListing]:
        """Return all DataZone listings matching the configured package asset type."""
        listings: list[DataZonePackageListing] = []
        for item in self._search_listings(""):
            if item.get("entityType") != self._config.asset_type_name:
                continue
            listing_id = str(item.get("listingId") or "")
            asset_id = str(item.get("entityId") or "")
            if not listing_id or not asset_id:
                continue
            listings.append(
                DataZonePackageListing(
                    listing_id=listing_id,
                    listing_revision=str(item.get("listingRevision") or ""),
                    asset_id=asset_id,
                    asset_revision=str(item.get("entityRevision") or ""),
                    name=str(item.get("name") or ""),
                    owner_project_id=str(item.get("owningProjectId") or ""),
                )
            )
        return listings

    def has_package_grant(self, *, project_id: str, quilt_uri: str) -> bool:
        listing = self.find_package_listing(quilt_uri)
        if listing is None:
            return False
        # The producing project has inherent access; no subscription is required.
        if listing.owner_project_id == project_id:
            return True
        return self._has_listing_grant(project_id=project_id, listing_id=listing.listing_id)

    def ensure_package_listing(
        self,
        quilt_uri: str,
        *,
        owner_project_id: str | None = None,
    ) -> DataZonePackageListing:
        effective_owner_project_id = owner_project_id or self._config.owner_project_id
        if not effective_owner_project_id:
            raise DataZoneError("DATAZONE_OWNER_PROJECT_ID is required")

        existing = self.find_package_listing(quilt_uri, owner_project_id=effective_owner_project_id)
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
                owningProjectIdentifier=effective_owner_project_id,
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

    def _get_asset_external_identifier(self, asset_id: str) -> str:
        try:
            response = self._client.get_asset(
                domainIdentifier=self._config.domain_id,
                identifier=asset_id,
            )
        except (ClientError, BotoCoreError):
            return ""
        if not isinstance(response, dict):
            return ""
        return str(response.get("externalIdentifier") or "")

    def find_project_for_principal(
        self,
        principal: str,
        *,
        project_ids: list[str],
    ) -> str | None:
        """Return the first project_id where principal is a member, or None.

        Checks projects in order — pass highest-privilege first so the most
        permissive match is returned when a user belongs to multiple projects.
        """
        for project_id in project_ids:
            if project_id and self._is_project_member(project_id=project_id, principal=principal):
                return project_id
        return None

    def _get_iam_arn_for_user_id(self, user_id: str) -> str | None:
        """Resolve a DataZone internal user ID to an IAM ARN via GetUserProfile."""
        try:
            resp = self._client.get_user_profile(
                domainIdentifier=self._config.domain_id,
                userIdentifier=user_id,
                type="IAM",
            )
            arn: str | None = resp.get("details", {}).get("iam", {}).get("arn")
            return arn
        except (ClientError, BotoCoreError):
            return None

    def _get_user_id_for_principal(self, principal: str) -> str | None:
        """Resolve an IAM ARN to a DataZone internal user ID via GetUserProfile."""
        try:
            resp = self._client.get_user_profile(
                domainIdentifier=self._config.domain_id,
                userIdentifier=principal,
                type="IAM",
            )
            user_id: str | None = resp.get("id")
            return user_id
        except (ClientError, BotoCoreError):
            return None

    def _resolve_membership_user_identifier(self, user_identifier: str) -> str:
        """Return the DataZone membership identifier for a user.

        DataZone membership mutation APIs expect a DataZone user ID, not an IAM ARN.
        We resolve IAM ARNs lazily and pass through already-resolved user IDs unchanged.
        """
        if user_identifier.startswith("arn:"):
            dz_user_id = self._get_user_id_for_principal(user_identifier)
            if dz_user_id is None:
                raise DataZoneError(f"failed to resolve DataZone user ID for {user_identifier}")
            return dz_user_id
        return user_identifier

    def list_project_members(self, project_id: str) -> list[str]:
        """Return IAM ARNs of all members of the given DataZone project."""
        next_token: str | None = None
        user_ids: list[str] = []
        while True:
            kwargs: dict[str, Any] = {
                "domainIdentifier": self._config.domain_id,
                "projectIdentifier": project_id,
                "maxResults": 50,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            try:
                response = self._client.list_project_memberships(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                raise DataZoneError(f"failed to list memberships for project {project_id}") from exc
            for member in response.get("members", []):
                uid = member.get("memberDetails", {}).get("user", {}).get("userId", "")
                if uid:
                    user_ids.append(uid)
            next_token = response.get("nextToken")
            if not next_token:
                break

        # Resolve DataZone user IDs to IAM ARNs
        arns: list[str] = []
        for uid in user_ids:
            arn = self._get_iam_arn_for_user_id(uid)
            if arn:
                arns.append(arn)
        return arns

    def _is_project_member(self, *, project_id: str, principal: str) -> bool:
        """Return True if principal (IAM ARN) is a member of the given project."""
        # Resolve the IAM ARN to a DataZone user ID once, then check membership.
        dz_user_id = self._get_user_id_for_principal(principal)
        if dz_user_id is None:
            return False

        next_token: str | None = None
        while True:
            kwargs: dict[str, Any] = {
                "domainIdentifier": self._config.domain_id,
                "projectIdentifier": project_id,
                "maxResults": 50,
            }
            if next_token:
                kwargs["nextToken"] = next_token
            try:
                response = self._client.list_project_memberships(**kwargs)
            except (ClientError, BotoCoreError) as exc:
                raise DataZoneError(f"failed to list memberships for project {project_id}") from exc
            for member in response.get("members", []):
                user_id = member.get("memberDetails", {}).get("user", {}).get("userId", "")
                if user_id == dz_user_id:
                    return True
            next_token = response.get("nextToken")
            if not next_token:
                return False

    def delete_project_membership(
        self,
        *,
        project_id: str,
        user_identifier: str,
    ) -> None:
        """Remove a DataZone user or IAM principal from a project membership."""
        membership_user_identifier = self._resolve_membership_user_identifier(user_identifier)
        try:
            self._client.delete_project_membership(
                domainIdentifier=self._config.domain_id,
                projectIdentifier=project_id,
                member={"userIdentifier": membership_user_identifier},
            )
        except (ClientError, BotoCoreError) as exc:
            error_code = ""
            if isinstance(exc, ClientError):
                error_code = exc.response.get("Error", {}).get("Code", "")
            if error_code != "ResourceNotFoundException":
                raise DataZoneError(
                    f"failed to remove {user_identifier} from project {project_id}"
                ) from exc

    def ensure_project_membership(
        self,
        *,
        project_id: str,
        user_identifier: str,
        designation: str = "PROJECT_CONTRIBUTOR",
    ) -> None:
        """Add a DataZone user or IAM principal as a DataZone project member."""
        membership_user_identifier = self._resolve_membership_user_identifier(user_identifier)
        try:
            self._client.create_project_membership(
                domainIdentifier=self._config.domain_id,
                projectIdentifier=project_id,
                member={"userIdentifier": membership_user_identifier},
                designation=designation,
            )
        except (ClientError, BotoCoreError) as exc:
            # Already a member — not an error
            error_code = ""
            error_message = ""
            if isinstance(exc, ClientError):
                error_code = exc.response.get("Error", {}).get("Code", "")
                error_message = str(exc.response.get("Error", {}).get("Message", ""))
            already_member = error_code == "ConflictException" or (
                error_code == "ValidationException" and "already in the project" in error_message
            )
            if not already_member:
                raise DataZoneError(
                    f"failed to add {user_identifier} to DataZone project {project_id}"
                ) from exc

    def ensure_project_package_grant(
        self,
        *,
        project_id: str,
        quilt_uri: str,
        owner_project_id: str | None = None,
    ) -> str:
        listing = self.ensure_package_listing(quilt_uri, owner_project_id=owner_project_id)
        # The producing project already has access; no subscription is required.
        if listing.owner_project_id == project_id:
            return listing.listing_id
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
        accept_error: Exception | None = None
        try:
            self._client.accept_subscription_request(
                domainIdentifier=self._config.domain_id,
                identifier=request_id,
                decisionComment="Approved by RAJA POC bootstrap",
            )
        except (ClientError, BotoCoreError) as exc:
            accept_error = exc
        if self._wait_for_listing_grant(project_id=project_id, listing_id=listing.listing_id):
            return listing.listing_id
        if accept_error is not None:
            raise DataZoneError(
                f"failed to accept subscription request {request_id} for project {project_id}"
            ) from accept_error
        raise DataZoneError(
            f"subscription request {request_id} did not become accepted for project {project_id}"
        )

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

    def _has_listing_grant(self, *, project_id: str, listing_id: str) -> bool:
        return (
            self._find_subscription_request(
                project_id=project_id,
                listing_id=listing_id,
                status="ACCEPTED",
            )
            is not None
        )

    def _wait_for_listing_grant(
        self,
        *,
        project_id: str,
        listing_id: str,
        timeout_seconds: float = 15.0,
    ) -> bool:
        deadline = time.time() + timeout_seconds
        while time.time() < deadline:
            if self._has_listing_grant(project_id=project_id, listing_id=listing_id):
                return True
            time.sleep(1)
        return self._has_listing_grant(project_id=project_id, listing_id=listing_id)

    def find_accepted_subscription(
        self,
        *,
        project_id: str,
        listing_id: str,
    ) -> dict[str, Any] | None:
        return self._find_subscription_request(
            project_id=project_id,
            listing_id=listing_id,
            status="ACCEPTED",
        )

    def list_subscription_requests(
        self,
        *,
        listing_ids: list[str] | None = None,
        statuses: tuple[str, ...] = ("ACCEPTED", "PENDING", "REJECTED"),
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        effective_listing_ids: list[str | None] = list(listing_ids) if listing_ids else [None]
        for listing_id in effective_listing_ids:
            for status in statuses:
                next_token: str | None = None
                while True:
                    kwargs: dict[str, Any] = {
                        "domainIdentifier": self._config.domain_id,
                        "status": status,
                        "maxResults": _SEARCH_PAGE_SIZE,
                    }
                    if listing_id:
                        kwargs["subscribedListingId"] = listing_id
                    elif self._config.owner_project_id:
                        kwargs["owningProjectId"] = self._config.owner_project_id
                    if next_token:
                        kwargs["nextToken"] = next_token
                    try:
                        response = self._client.list_subscription_requests(**kwargs)
                    except (ClientError, BotoCoreError) as exc:
                        raise DataZoneError(
                            "failed to list DataZone subscription requests"
                        ) from exc
                    for item in response.get("items", []):
                        if not isinstance(item, dict):
                            continue
                        request_id = str(item.get("id") or "")
                        if request_id and request_id in seen_ids:
                            continue
                        normalized = dict(item)
                        normalized["status"] = str(item.get("status") or status)
                        items.append(normalized)
                        if request_id:
                            seen_ids.add(request_id)
                    next_token = response.get("nextToken")
                    if not next_token:
                        break
        return items

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
