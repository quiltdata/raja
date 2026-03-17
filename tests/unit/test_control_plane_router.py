"""Unit tests for control plane router endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from raja.server.routers import control_plane


def _make_request(request_id: str | None = None) -> Request:
    """Create a mock Request object."""
    headers = []
    if request_id:
        headers.append((b"x-request-id", request_id.encode()))
    scope = {"type": "http", "headers": headers}
    return Request(scope)


def _mock_datazone_with_scopes(scopes: list[str]) -> MagicMock:
    """Return a datazone mock whose find_project_for_principal returns 'proj-owner'."""
    datazone = MagicMock()
    with patch.object(control_plane, "_derive_principal_scopes", return_value=scopes):
        return datazone


def test_issue_token_raja_type():
    """Test issuing a RAJA token."""
    payload = control_plane.TokenRequest(principal="alice", token_type="raja")
    with patch.object(
        control_plane, "_derive_principal_scopes", return_value=["Document:doc1:read"]
    ):
        response = control_plane.issue_token(
            _make_request(),
            payload,
            datazone=MagicMock(),
            secret="secret",
        )

    assert response["principal"] == "alice"
    assert "token" in response
    assert response["scopes"] == ["Document:doc1:read"]


def test_issue_token_rajee_type():
    """Test issuing a RAJEE token with scopes."""
    request = MagicMock()
    request.headers = MagicMock()
    request.headers.get = Mock(return_value=None)
    base_url_mock = Mock()
    base_url_mock.__str__ = Mock(return_value="https://api.example.com/")
    request.base_url = base_url_mock

    payload = control_plane.TokenRequest(principal="alice", token_type="rajee")
    with patch.object(
        control_plane, "_derive_principal_scopes", return_value=["S3Object:bucket:key:s3:GetObject"]
    ):
        response = control_plane.issue_token(
            request,
            payload,
            datazone=MagicMock(),
            secret="secret",
        )

    assert response["principal"] == "alice"
    assert "token" in response
    assert "scopes" in response


def test_issue_token_invalid_type():
    """Test that issuing a token with invalid type raises HTTPException."""
    payload = control_plane.TokenRequest(principal="alice", token_type="invalid")
    with patch.object(
        control_plane, "_derive_principal_scopes", return_value=["Document:doc1:read"]
    ):
        with pytest.raises(HTTPException) as exc_info:
            control_plane.issue_token(
                _make_request(),
                payload,
                datazone=MagicMock(),
                secret="secret",
            )

    assert exc_info.value.status_code == 400
    assert "Unsupported token_type" in exc_info.value.detail


def test_issue_package_token_allows():
    datazone = MagicMock()

    payload = control_plane.PackageTokenRequest(
        principal='Role::"analyst"',
        resource='Package::"quilt+s3://registry#package=my/pkg@abc123def456"',
        action="quilt:ReadPackage",
    )
    with patch.object(control_plane, "_authorize_package_with_datazone", return_value=True):
        response = control_plane.issue_package_token(
            _make_request(),
            payload,
            datazone=datazone,
            secret="secret",
        )

    assert response["principal"] == 'Role::"analyst"'
    assert response["quilt_uri"] == "quilt+s3://registry#package=my/pkg@abc123def456"
    assert "token" in response


def test_issue_package_token_denied_by_policy():
    datazone = MagicMock()

    payload = control_plane.PackageTokenRequest(
        principal='Role::"analyst"',
        resource='Package::"quilt+s3://registry#package=my/pkg@abc123def456"',
        action="quilt:ReadPackage",
    )
    with pytest.raises(HTTPException) as exc_info:
        with patch.object(control_plane, "_authorize_package_with_datazone", return_value=False):
            control_plane.issue_package_token(
                _make_request(),
                payload,
                datazone=datazone,
                secret="secret",
            )

    assert exc_info.value.status_code == 403


def test_issue_package_token_rejects_write_action():
    payload = control_plane.PackageTokenRequest(
        principal='Role::"analyst"',
        resource='Package::"quilt+s3://registry#package=my/pkg@abc123def456"',
        action="quilt:WritePackage",
    )
    with pytest.raises(HTTPException) as exc_info:
        control_plane.issue_package_token(
            _make_request(),
            payload,
            datazone=MagicMock(),
            secret="secret",
        )

    assert exc_info.value.status_code == 400


def test_issue_translation_token_allows():
    datazone = MagicMock()

    payload = control_plane.TranslationTokenRequest(
        principal='Role::"analyst"',
        resource='Package::"quilt+s3://registry#package=my/pkg@abc123def456"',
        action="quilt:ReadPackage",
        logical_s3_path="s3://logical-bucket/logical/file.csv",
    )
    with patch.object(control_plane, "_authorize_package_with_datazone", return_value=True):
        response = control_plane.issue_translation_token(
            _make_request(),
            payload,
            datazone=datazone,
            secret="secret",
        )

    assert response["logical_bucket"] == "logical-bucket"
    assert response["logical_key"] == "logical/file.csv"
    assert response["quilt_uri"] == "quilt+s3://registry#package=my/pkg@abc123def456"
    assert "token" in response


def test_list_principals_with_limit():
    """Test listing principals returns members from DataZone projects."""
    datazone = MagicMock()
    with patch.object(control_plane, "_datazone_service") as factory:
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            config = MagicMock()
            config.owner_project_id = "proj-owner"
            config.users_project_id = "proj-users"
            config.guests_project_id = "proj-guests"
            mock_config_cls.from_env.return_value = config
            service = factory.return_value
            service.list_project_members.return_value = ["alice", "bob"]
            response = control_plane.list_principals(limit=10, datazone=datazone)

    assert len(response["principals"]) >= 1
    assert all("principal" in p for p in response["principals"])


def test_list_principals_without_limit():
    """Test listing principals without a limit."""
    datazone = MagicMock()
    with patch.object(control_plane, "_datazone_service") as factory:
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            config = MagicMock()
            config.owner_project_id = "proj-owner"
            config.users_project_id = ""
            config.guests_project_id = ""
            mock_config_cls.from_env.return_value = config
            service = factory.return_value
            service.list_project_members.return_value = ["alice"]
            response = control_plane.list_principals(limit=None, datazone=datazone)

    assert len(response["principals"]) == 1
    assert response["principals"][0]["principal"] == "alice"
    assert response["principal_summary"] == [
        {"principal": "alice", "project_ids": ["proj-owner"], "project_names": ["Owner"]}
    ]


def test_list_principals_preserves_multi_project_memberships():
    """A principal in two projects should remain visible in both rows."""
    datazone = MagicMock()
    with patch.object(control_plane, "_datazone_service") as factory:
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            config = MagicMock()
            config.owner_project_id = "proj-owner"
            config.users_project_id = "proj-users"
            config.guests_project_id = ""
            mock_config_cls.from_env.return_value = config
            service = factory.return_value
            service.list_project_members.side_effect = [["alice"], ["alice"]]
            response = control_plane.list_principals(limit=None, datazone=datazone)

    assert response["principals"] == [
        {
            "principal": "alice",
            "datazone_project_id": "proj-owner",
            "datazone_project_name": "Owner",
            "scopes": ["*:*:*"],
            "scope_count": 1,
            "last_token_issued": None,
        },
        {
            "principal": "alice",
            "datazone_project_id": "proj-users",
            "datazone_project_name": "Users",
            "scopes": ["S3Object:*:*"],
            "scope_count": 1,
            "last_token_issued": None,
        },
    ]
    assert response["principal_summary"] == [
        {
            "principal": "alice",
            "project_ids": ["proj-owner", "proj-users"],
            "project_names": ["Owner", "Users"],
        }
    ]


def test_create_principal():
    """Test creating a principal adds them to the correct DataZone project."""
    datazone = MagicMock()

    request = control_plane.PrincipalRequest(
        principal="alice", scopes=["Document:doc1:read", "Document:doc2:write"]
    )
    with patch.object(control_plane, "datazone_enabled", return_value=True):
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            config = MagicMock()
            config.owner_project_id = "proj-owner"
            config.users_project_id = "proj-users"
            config.guests_project_id = "proj-guests"
            mock_config_cls.from_env.return_value = config
            with patch.object(control_plane, "project_id_for_scopes", return_value="proj-users"):
                with patch.object(control_plane, "_datazone_service"):
                    response = control_plane.create_principal(request, datazone=datazone)

    assert response["principal"] == "alice"
    assert "datazone_project_id" in response


def test_create_principal_empty_scopes():
    """Test creating a principal with no scopes lands in guests project."""
    datazone = MagicMock()
    request = control_plane.PrincipalRequest(principal="alice", scopes=[])
    with patch.object(control_plane, "datazone_enabled", return_value=True):
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            config = MagicMock()
            config.guests_project_id = "proj-guests"
            mock_config_cls.from_env.return_value = config
            with patch.object(control_plane, "project_id_for_scopes", return_value="proj-guests"):
                with patch.object(control_plane, "_datazone_service"):
                    response = control_plane.create_principal(request, datazone=datazone)

    assert response["principal"] == "alice"


def test_delete_principal():
    """Test deleting a principal removes them from their DataZone project."""
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        config = MagicMock()
        config.owner_project_id = "proj-owner"
        config.users_project_id = "proj-users"
        config.guests_project_id = "proj-guests"
        mock_config_cls.from_env.return_value = config
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            service.find_project_for_principal.return_value = "proj-owner"
            response = control_plane.delete_principal("alice", datazone=datazone)

    assert "Removed" in response["message"]
    service.delete_project_membership.assert_called_once()


def test_delete_principal_respects_explicit_project_id():
    """Deleting from a specific project must not re-resolve membership."""
    datazone = MagicMock()
    with patch.object(control_plane, "_datazone_service") as factory:
        service = factory.return_value
        response = control_plane.delete_principal(
            "alice",
            project_id="proj-users",
            datazone=datazone,
        )

    assert "Removed" in response["message"]
    service.find_project_for_principal.assert_not_called()
    service.delete_project_membership.assert_called_once_with(
        project_id="proj-users",
        user_identifier="alice",
    )


def test_list_policies_without_statements():
    datazone = MagicMock()
    response_payload = [
        {
            "listingId": "l1",
            "name": "demo/package-grant",
            "entityType": "QuiltPackage",
            "owningProjectId": "proj-owner",
        }
    ]
    with patch.object(control_plane, "_datazone_service") as factory:
        service = factory.return_value
        service._config.asset_type_name = "QuiltPackage"
        service._search_listings.return_value = response_payload
        response = control_plane.list_policies(include_statements=False, datazone=datazone)

    assert len(response["policies"]) == 1
    assert response["policies"][0]["policyId"] == "l1"


def test_list_policies_with_statements():
    datazone = MagicMock()
    response_payload = [
        {
            "listingId": "l1",
            "name": "demo/package-grant",
            "entityType": "QuiltPackage",
            "owningProjectId": "proj-owner",
        }
    ]
    with patch.object(control_plane, "_datazone_service") as factory:
        service = factory.return_value
        service._config.asset_type_name = "QuiltPackage"
        service._search_listings.return_value = response_payload
        response = control_plane.list_policies(include_statements=True, datazone=datazone)

    assert len(response["policies"]) == 1
    assert "definition" in response["policies"][0]


def test_list_policies_skips_missing_policy_id():
    datazone = MagicMock()
    response_payload = [
        {"listingId": "l1", "name": "demo/package-grant", "entityType": "QuiltPackage"},
        {"name": "skip-me", "entityType": "OtherType"},
    ]
    with patch.object(control_plane, "_datazone_service") as factory:
        service = factory.return_value
        service._config.asset_type_name = "QuiltPackage"
        service._search_listings.return_value = response_payload
        response = control_plane.list_policies(include_statements=True, datazone=datazone)

    assert len(response["policies"]) == 1


def test_get_admin_structure_reads_domain_and_asset_type() -> None:
    request = MagicMock()
    request.headers = {"host": "api.example.com", "x-forwarded-proto": "https"}
    request.url.scheme = "https"
    request.url.netloc = "api.example.com"
    request.url.hostname = "api.example.com"
    request.scope = {"aws.event": {"requestContext": {"stage": "prod"}}}
    datazone = MagicMock()
    datazone.get_domain.return_value = {
        "name": "demo-domain",
        "portalUrl": "https://dzd-123.sagemaker.us-east-1.on.aws",
    }
    datazone.get_asset_type.return_value = {"name": "QuiltPackage", "revision": "2"}

    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        config = MagicMock()
        config.domain_id = "dzd-123"
        config.owner_project_id = "proj-owner"
        config.users_project_id = "proj-users"
        config.guests_project_id = "proj-guests"
        config.owner_environment_id = "env-owner"
        config.users_environment_id = "env-users"
        config.guests_environment_id = "env-guests"
        config.asset_type_name = "QuiltPackage"
        config.asset_type_revision = "2"
        mock_config_cls.from_env.return_value = config
        with patch.dict(
            "os.environ",
            {"AWS_REGION": "us-east-1"},
            clear=False,
        ):
            with patch.object(
                control_plane,
                "_resolve_runtime_config",
                return_value={
                    "registry": "s3://demo-registry",
                    "rajee_endpoint": "https://rajee.example.com",
                    "rale_authorizer_url": "https://authorizer.example.com",
                    "rale_router_url": "https://router.example.com",
                },
            ):
                with patch.object(
                    control_plane, "get_jwks", return_value={"keys": [{"kid": "kid-1"}]}
                ):
                    with patch.object(
                        control_plane,
                        "_probe_endpoint",
                        return_value={"reachable": True, "status": "ok"},
                    ):
                        response = control_plane.get_admin_structure(
                            request=request,
                            datazone=datazone,
                            secret="secret",
                        )

    datazone.get_domain.assert_called_once_with(identifier="dzd-123")
    datazone.get_asset_type.assert_called_once_with(
        domainIdentifier="dzd-123",
        identifier="QuiltPackage",
        revision="2",
    )
    assert response["datazone"]["domain"]["status"] == "ok"
    assert response["datazone"]["asset_type"]["status"] == "ok"
    assert (
        response["datazone"]["domain"]["portal_url"] == "https://dzd-123.sagemaker.us-east-1.on.aws"
    )
    assert response["datazone"]["owner_project"]["portal_url"].endswith(
        "/projects/proj-owner/overview"
    )
    assert response["datazone"]["owner_project"]["environment_id"] == "env-owner"
    assert response["datazone"]["owner_project"]["environment_url"].endswith(
        "/environments/env-owner"
    )
    assert response["datazone"]["users_project"]["portal_url"].endswith(
        "/projects/proj-users/overview"
    )
    assert response["datazone"]["users_project"]["environment_id"] == "env-users"
    assert response["datazone"]["users_project"]["environment_url"].endswith(
        "/environments/env-users"
    )
    assert response["datazone"]["guests_project"]["portal_url"].endswith(
        "/projects/proj-guests/overview"
    )
    assert response["datazone"]["guests_project"]["environment_id"] == "env-guests"
    assert response["datazone"]["guests_project"]["environment_url"].endswith(
        "/environments/env-guests"
    )
    assert response["stack"]["server"]["url"] == "https://api.example.com/prod"
    assert response["stack"]["rale_authorizer"]["url"] == "https://authorizer.example.com"
    assert response["stack"]["rale_router"]["url"] == "https://router.example.com"
    assert response["stack"]["jwks"]["url"] == "https://api.example.com/prod/.well-known/jwks.json"


def test_get_access_graph_includes_listing_project_links_and_summary() -> None:
    datazone = MagicMock()
    listing = MagicMock()
    listing.listing_id = "listing-1"
    listing.name = "demo/package"
    listing.owner_project_id = "proj-owner"

    with patch.object(control_plane, "_datazone_service") as factory:
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            config = MagicMock()
            config.domain_id = "dzd-123"
            config.owner_project_id = "proj-owner"
            config.users_project_id = "proj-users"
            config.guests_project_id = "proj-guests"
            config.asset_type_name = "QuiltPackage"
            mock_config_cls.from_env.return_value = config
            service = factory.return_value
            service.list_package_listings.return_value = [listing]
            service.find_accepted_subscription.return_value = None
            service.list_subscription_requests.return_value = [
                {
                    "id": "sub-1",
                    "status": "ACCEPTED",
                    "subscribedPrincipals": [{"project": {"id": "proj-users"}}],
                    "subscribedListings": [{"id": "listing-1"}],
                }
            ]
            datazone.get_domain.return_value = {
                "portalUrl": "https://dzd-123.sagemaker.us-east-1.on.aws"
            }
            with patch.object(
                control_plane,
                "list_principals",
                return_value={
                    "principals": [
                        {
                            "principal": "alice",
                            "datazone_project_id": "proj-owner",
                            "datazone_project_name": "Owner",
                            "scopes": ["*:*:*"],
                            "scope_count": 1,
                            "last_token_issued": None,
                        }
                    ],
                    "principal_summary": [
                        {
                            "principal": "alice",
                            "project_ids": ["proj-owner"],
                            "project_names": ["Owner"],
                        }
                    ],
                },
            ):
                with patch.dict("os.environ", {"AWS_REGION": "us-east-1"}, clear=False):
                    response = control_plane.get_access_graph(datazone=datazone)

    assert response["principal_summary"] == [
        {
            "principal": "alice",
            "project_ids": ["proj-owner"],
            "project_names": ["Owner"],
        }
    ]
    assert response["packages"][0]["owner_project_url"].endswith("/projects/proj-owner/overview")
    assert response["subscriptions"] == [
        {
            "package_name": "demo/package",
            "owner_project_id": "proj-owner",
            "owner_project_name": "Owner",
            "consumer_project_id": "proj-users",
            "consumer_project_name": "Users",
            "status": "ACCEPTED",
            "subscription_id": "sub-1",
            "subscription_url": "https://dzd-123.sagemaker.us-east-1.on.aws/projects/proj-owner/catalog/subscriptionRequests/incoming?status=APPROVED",
        }
    ]


def test_studio_subscription_requests_url_maps_status() -> None:
    result = control_plane._studio_subscription_requests_url(
        portal_url="https://dzd-123.sagemaker.us-east-1.on.aws",
        project_id="proj-owner",
        status="ACCEPTED",
    )

    assert result == (
        "https://dzd-123.sagemaker.us-east-1.on.aws/projects/proj-owner/"
        "catalog/subscriptionRequests/incoming?status=APPROVED"
    )


def test_probe_endpoint_appends_ready_path_to_probe_url() -> None:
    response = MagicMock()
    response.status_code = 200

    with patch.object(control_plane.httpx, "get", return_value=response) as get_mock:
        result = control_plane._probe_endpoint(
            "https://authorizer.example.com/",
            ready_path="health",
        )

    get_mock.assert_called_once_with(
        "https://authorizer.example.com/health",
        timeout=5.0,
        follow_redirects=False,
    )
    assert result["status"] == "ok"
    assert result["url"] == "https://authorizer.example.com/health"


def test_get_jwks():
    """Test JWKS endpoint returns correct format."""
    response = control_plane.get_jwks(secret="test-secret")

    assert "keys" in response
    assert len(response["keys"]) == 1
    key = response["keys"][0]
    assert key["kty"] == "oct"
    assert key["kid"] == "raja-jwt-key"
    assert key["alg"] == "HS256"
    assert "k" in key


def test_probe_endpoint_marks_client_errors_warn() -> None:
    response = MagicMock()
    response.status_code = 400

    with patch.object(control_plane.httpx, "get", return_value=response):
        result = control_plane._probe_endpoint("https://authorizer.example.com")

    assert result["reachable"] is True
    assert result["status"] == "warn"
    assert result["status_code"] == 400


def test_require_env_raises_when_missing():
    with pytest.raises(RuntimeError, match="TEST_VAR is required"):
        control_plane._require_env(None, "TEST_VAR")


def test_require_env_returns_value():
    result = control_plane._require_env("test-value", "TEST_VAR")
    assert result == "test-value"


def test_get_request_id_from_x_request_id():
    request = _make_request(request_id="req-123")
    request_id = control_plane._get_request_id(request)
    assert request_id == "req-123"


def test_get_request_id_generates_uuid():
    request = _make_request()
    request_id = control_plane._get_request_id(request)
    assert len(request_id) > 0
    assert "-" in request_id


def test_rotate_secret_records_succeeded_operation() -> None:
    with patch.object(control_plane, "_perform_secret_rotation", return_value="v-new"):
        response = control_plane.rotate_secret()

    assert response["status"] == "SUCCEEDED"
    assert "operation_id" in response


def test_rotate_secret_records_failed_operation() -> None:
    with patch.object(control_plane, "_perform_secret_rotation", side_effect=RuntimeError("boom")):
        response = control_plane.rotate_secret()

    assert response["status"] == "FAILED"
    assert "operation_id" in response
