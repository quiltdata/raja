"""Unit tests for src/raja/datazone/service.py using manual stubs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from botocore.exceptions import ClientError

from raja.datazone import (
    DataZoneConfig,
    DataZoneError,
    DataZoneService,
    datazone_enabled,
    project_name_for_principal,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DOMAIN = "dzd_test123"
_OWNER_PROJECT = "prj_owner"
_ASSET_TYPE = "QuiltPackage"
_ASSET_TYPE_REV = "1"

_QUILT_URI = "quilt+s3://my-bucket#package=demo/pkg@abc123"


def _config(**kwargs: str) -> DataZoneConfig:
    defaults: dict[str, str] = {
        "domain_id": _DOMAIN,
        "owner_project_id": _OWNER_PROJECT,
        "asset_type_name": _ASSET_TYPE,
        "asset_type_revision": _ASSET_TYPE_REV,
    }
    defaults.update(kwargs)
    return DataZoneConfig(**defaults)


def _service(client: MagicMock, **kwargs: str) -> DataZoneService:
    return DataZoneService(client=client, config=_config(**kwargs))


def _listing_item(
    *,
    listing_id: str = "lst_abc",
    asset_id: str = "ast_abc",
    name: str = "demo/pkg",
    asset_type: str = _ASSET_TYPE,
    project_id: str = _OWNER_PROJECT,
) -> dict:
    return {
        "listingId": listing_id,
        "listingRevision": "1",
        "entityId": asset_id,
        "entityRevision": "1",
        "entityType": asset_type,
        "name": name,
        "owningProjectId": project_id,
    }


def _subscription_item(
    *,
    request_id: str = "sub_abc",
    project_id: str = "prj_consumer",
    listing_id: str = "lst_abc",
) -> dict:
    return {
        "id": request_id,
        "subscribedPrincipals": [{"project": {"id": project_id}}],
        "subscribedListings": [{"id": listing_id}],
    }


# ---------------------------------------------------------------------------
# datazone_enabled
# ---------------------------------------------------------------------------


def test_datazone_enabled_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAZONE_DOMAIN_ID", "dzd_xyz")
    assert datazone_enabled() is True


def test_datazone_enabled_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAZONE_DOMAIN_ID", raising=False)
    assert datazone_enabled() is False


# ---------------------------------------------------------------------------
# project_name_for_principal
# ---------------------------------------------------------------------------


def test_project_name_plain() -> None:
    name = project_name_for_principal("alice")
    assert name.startswith("raja-principal-alice-")
    assert len(name) <= 64


def test_project_name_cedar_syntax() -> None:
    name = project_name_for_principal('User::"alice"')
    assert "alice" in name


def test_project_name_deterministic() -> None:
    assert project_name_for_principal("alice") == project_name_for_principal("alice")


def test_project_name_different_principals() -> None:
    assert project_name_for_principal("alice") != project_name_for_principal("bob")


def test_project_name_empty_raises() -> None:
    with pytest.raises(ValueError):
        project_name_for_principal("")


# ---------------------------------------------------------------------------
# DataZoneConfig.from_env
# ---------------------------------------------------------------------------


def test_config_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATAZONE_DOMAIN_ID", "dzd_env")
    monkeypatch.setenv("DATAZONE_OWNER_PROJECT_ID", "prj_env")
    monkeypatch.setenv("DATAZONE_PACKAGE_ASSET_TYPE", "MyType")
    monkeypatch.setenv("DATAZONE_PACKAGE_ASSET_TYPE_REVISION", "2")
    cfg = DataZoneConfig.from_env()
    assert cfg.domain_id == "dzd_env"
    assert cfg.owner_project_id == "prj_env"
    assert cfg.asset_type_name == "MyType"
    assert cfg.asset_type_revision == "2"


def test_config_from_env_missing_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATAZONE_DOMAIN_ID", raising=False)
    with pytest.raises(DataZoneError):
        DataZoneConfig.from_env()


# ---------------------------------------------------------------------------
# find_package_listing
# ---------------------------------------------------------------------------


def test_find_package_listing_found() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item()}],
        "nextToken": None,
    }
    svc = _service(client)
    listing = svc.find_package_listing(_QUILT_URI)
    assert listing is not None
    assert listing.listing_id == "lst_abc"
    assert listing.name == "demo/pkg"


def test_find_package_listing_wrong_type() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item(asset_type="OtherType")}],
        "nextToken": None,
    }
    svc = _service(client)
    assert svc.find_package_listing(_QUILT_URI) is None


def test_find_package_listing_name_mismatch() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item(name="other/pkg")}],
        "nextToken": None,
    }
    svc = _service(client)
    assert svc.find_package_listing(_QUILT_URI) is None


def test_find_package_listing_api_error() -> None:
    client = MagicMock()
    client.search_listings.side_effect = ClientError(
        {"Error": {"Code": "AccessDeniedException", "Message": "no"}}, "SearchListings"
    )
    svc = _service(client)
    with pytest.raises(DataZoneError):
        svc.find_package_listing(_QUILT_URI)


def test_find_package_listing_pagination() -> None:
    client = MagicMock()
    client.search_listings.side_effect = [
        {"items": [], "nextToken": "tok1"},
        {"items": [{"assetListing": _listing_item()}], "nextToken": None},
    ]
    svc = _service(client)
    listing = svc.find_package_listing(_QUILT_URI)
    assert listing is not None
    assert client.search_listings.call_count == 2


# ---------------------------------------------------------------------------
# has_package_grant
# ---------------------------------------------------------------------------


def test_has_package_grant_no_listing() -> None:
    client = MagicMock()
    client.search_listings.return_value = {"items": [], "nextToken": None}
    svc = _service(client)
    assert svc.has_package_grant(project_id="prj_x", quilt_uri=_QUILT_URI) is False


def test_has_package_grant_no_subscription() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item()}],
        "nextToken": None,
    }
    client.list_subscription_requests.return_value = {"items": [], "nextToken": None}
    svc = _service(client)
    assert svc.has_package_grant(project_id="prj_x", quilt_uri=_QUILT_URI) is False


def test_has_package_grant_accepted() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item(listing_id="lst_abc")}],
        "nextToken": None,
    }
    client.list_subscription_requests.return_value = {
        "items": [_subscription_item(project_id="prj_x", listing_id="lst_abc")],
        "nextToken": None,
    }
    svc = _service(client)
    assert svc.has_package_grant(project_id="prj_x", quilt_uri=_QUILT_URI) is True


def test_has_package_grant_wrong_project() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item(listing_id="lst_abc")}],
        "nextToken": None,
    }
    client.list_subscription_requests.return_value = {
        "items": [_subscription_item(project_id="prj_other", listing_id="lst_abc")],
        "nextToken": None,
    }
    svc = _service(client)
    assert svc.has_package_grant(project_id="prj_x", quilt_uri=_QUILT_URI) is False


# ---------------------------------------------------------------------------
# ensure_project_for_principal
# ---------------------------------------------------------------------------


def test_ensure_project_creates_new() -> None:
    client = MagicMock()
    client.list_projects.return_value = {"items": [], "nextToken": None}
    client.create_project.return_value = {"id": "prj_new"}
    svc = _service(client)
    result = svc.ensure_project_for_principal("alice")
    assert result["project_id"] == "prj_new"
    client.create_project.assert_called_once()


def test_ensure_project_returns_existing() -> None:
    expected_name = project_name_for_principal("alice")
    client = MagicMock()
    client.list_projects.return_value = {
        "items": [{"id": "prj_existing", "name": expected_name}],
        "nextToken": None,
    }
    svc = _service(client)
    result = svc.ensure_project_for_principal("alice")
    assert result["project_id"] == "prj_existing"
    client.create_project.assert_not_called()


def test_ensure_project_create_error() -> None:
    client = MagicMock()
    client.list_projects.return_value = {"items": [], "nextToken": None}
    client.create_project.side_effect = ClientError(
        {"Error": {"Code": "ConflictException", "Message": "exists"}}, "CreateProject"
    )
    svc = _service(client)
    with pytest.raises(DataZoneError):
        svc.ensure_project_for_principal("alice")


# ---------------------------------------------------------------------------
# ensure_package_listing
# ---------------------------------------------------------------------------


def test_ensure_package_listing_returns_existing() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item()}],
        "nextToken": None,
    }
    svc = _service(client)
    listing = svc.ensure_package_listing(_QUILT_URI)
    assert listing.listing_id == "lst_abc"
    client.create_asset.assert_not_called()


def test_ensure_package_listing_requires_owner_project() -> None:
    client = MagicMock()
    client.search_listings.return_value = {"items": [], "nextToken": None}
    svc = _service(client, owner_project_id="")
    with pytest.raises(DataZoneError, match="DATAZONE_OWNER_PROJECT_ID"):
        svc.ensure_package_listing(_QUILT_URI)


def test_ensure_package_listing_create_asset_error() -> None:
    client = MagicMock()
    client.search_listings.return_value = {"items": [], "nextToken": None}
    client.create_asset.side_effect = ClientError(
        {"Error": {"Code": "ValidationException", "Message": "bad"}}, "CreateAsset"
    )
    svc = _service(client)
    with pytest.raises(DataZoneError):
        svc.ensure_package_listing(_QUILT_URI)


def test_ensure_package_listing_publish_error() -> None:
    client = MagicMock()
    client.search_listings.return_value = {"items": [], "nextToken": None}
    client.create_asset.return_value = {"id": "ast_new", "revision": "1"}
    client.create_listing_change_set.side_effect = ClientError(
        {"Error": {"Code": "InternalServerException", "Message": "oops"}},
        "CreateListingChangeSet",
    )
    svc = _service(client)
    with pytest.raises(DataZoneError):
        svc.ensure_package_listing(_QUILT_URI)


# ---------------------------------------------------------------------------
# ensure_project_package_grant
# ---------------------------------------------------------------------------


def test_ensure_grant_already_granted() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item(listing_id="lst_abc")}],
        "nextToken": None,
    }
    client.list_subscription_requests.return_value = {
        "items": [_subscription_item(project_id="prj_x", listing_id="lst_abc")],
        "nextToken": None,
    }
    svc = _service(client)
    listing_id = svc.ensure_project_package_grant(project_id="prj_x", quilt_uri=_QUILT_URI)
    assert listing_id == "lst_abc"
    client.create_subscription_request.assert_not_called()
    client.accept_subscription_request.assert_not_called()


def test_ensure_grant_creates_and_accepts() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item(listing_id="lst_abc")}],
        "nextToken": None,
    }
    # First call: check ACCEPTED (none), second call: check PENDING (none)
    client.list_subscription_requests.return_value = {"items": [], "nextToken": None}
    client.create_subscription_request.return_value = {"id": "sub_new"}
    client.accept_subscription_request.return_value = {}

    svc = _service(client)
    listing_id = svc.ensure_project_package_grant(project_id="prj_x", quilt_uri=_QUILT_URI)

    assert listing_id == "lst_abc"
    client.create_subscription_request.assert_called_once()
    client.accept_subscription_request.assert_called_once_with(
        domainIdentifier=_DOMAIN,
        identifier="sub_new",
        decisionComment="Approved by RAJA POC bootstrap",
    )


def test_ensure_grant_reuses_pending_request() -> None:
    client = MagicMock()
    client.search_listings.return_value = {
        "items": [{"assetListing": _listing_item(listing_id="lst_abc")}],
        "nextToken": None,
    }
    # ACCEPTED check returns nothing; PENDING check returns existing request
    client.list_subscription_requests.side_effect = [
        {"items": [], "nextToken": None},  # ACCEPTED check
        {
            "items": [
                _subscription_item(
                    request_id="sub_existing", project_id="prj_x", listing_id="lst_abc"
                )
            ],
            "nextToken": None,
        },  # PENDING check
    ]
    client.accept_subscription_request.return_value = {}

    svc = _service(client)
    svc.ensure_project_package_grant(project_id="prj_x", quilt_uri=_QUILT_URI)

    client.create_subscription_request.assert_not_called()
    client.accept_subscription_request.assert_called_once_with(
        domainIdentifier=_DOMAIN,
        identifier="sub_existing",
        decisionComment="Approved by RAJA POC bootstrap",
    )
