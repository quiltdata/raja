"""Unit tests for control plane router endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock, Mock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from raja.datazone import DataZoneConfig, ProjectConfig
from raja.server.routers import control_plane


def _make_request(request_id: str | None = None) -> Request:
    """Create a mock Request object."""
    headers = []
    if request_id:
        headers.append((b"x-request-id", request_id.encode()))
    scope = {"type": "http", "headers": headers}
    return Request(scope)


def _config(
    *,
    include_second: bool = True,
    include_third: bool = True,
) -> DataZoneConfig:
    projects = {
        "slot-a": ProjectConfig(
            project_id="proj-alpha",
            project_label="Alpha",
            environment_id="env-alpha",
        ),
    }
    if include_second:
        projects["slot-b"] = ProjectConfig(
            project_id="proj-bio",
            project_label="Bio",
            environment_id="env-bio",
        )
    if include_third:
        projects["slot-c"] = ProjectConfig(
            project_id="proj-compute",
            project_label="Compute",
            environment_id="env-compute",
        )
    return DataZoneConfig(domain_id="dzd-123", projects=projects)


def test_issue_token_raja_type():
    """Test issuing a RAJA token."""
    payload = control_plane.TokenRequest(principal="alice", token_type="raja")
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config(include_second=False, include_third=False)
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            service.find_project_for_principal.return_value = "proj-alpha"
            response = control_plane.issue_token(
                _make_request(),
                payload,
                datazone=datazone,
                secret="secret",
            )

    assert response["principal"] == "alice"
    assert "token" in response
    assert "scopes" not in response


def test_issue_token_rajee_type():
    """Test issuing a RAJEE token."""
    request = MagicMock()
    request.headers = MagicMock()
    request.headers.get = Mock(return_value=None)
    base_url_mock = Mock()
    base_url_mock.__str__ = Mock(return_value="https://api.example.com/")
    request.base_url = base_url_mock

    payload = control_plane.TokenRequest(principal="alice", token_type="rajee")
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config(include_second=False, include_third=False)
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            service.find_project_for_principal.return_value = "proj-alpha"
            response = control_plane.issue_token(
                request,
                payload,
                datazone=datazone,
                secret="secret",
            )

    assert response["principal"] == "alice"
    assert "token" in response
    assert "scopes" not in response


def test_issue_token_invalid_type():
    """Test that issuing a token with invalid type raises HTTPException."""
    payload = control_plane.TokenRequest(principal="alice", token_type="invalid")
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config(include_second=False, include_third=False)
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            service.find_project_for_principal.return_value = "proj-alpha"
            with pytest.raises(HTTPException) as exc_info:
                control_plane.issue_token(
                    _make_request(),
                    payload,
                    datazone=datazone,
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
            mock_config_cls.from_env.return_value = _config()
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
            mock_config_cls.from_env.return_value = _config(
                include_second=False,
                include_third=False,
            )
            service = factory.return_value
            service.list_project_members.return_value = ["alice"]
            response = control_plane.list_principals(limit=None, datazone=datazone)

    assert len(response["principals"]) == 1
    assert response["principals"][0]["principal"] == "alice"
    assert response["principal_summary"] == [
        {"principal": "alice", "project_ids": ["proj-alpha"], "project_names": ["Alpha"]}
    ]


def test_list_principals_preserves_multi_project_memberships():
    """A principal in two projects should remain visible in both rows."""
    datazone = MagicMock()
    with patch.object(control_plane, "_datazone_service") as factory:
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            mock_config_cls.from_env.return_value = _config(include_third=False)
            service = factory.return_value
            service.list_project_members.side_effect = [["alice"], ["alice"]]
            response = control_plane.list_principals(limit=None, datazone=datazone)

    assert response["principals"] == [
        {
            "principal": "alice",
            "datazone_project_id": "proj-alpha",
            "datazone_project_name": "Alpha",
            "last_token_issued": None,
        },
        {
            "principal": "alice",
            "datazone_project_id": "proj-bio",
            "datazone_project_name": "Bio",
            "last_token_issued": None,
        },
    ]
    assert response["principal_summary"] == [
        {
            "principal": "alice",
            "project_ids": ["proj-alpha", "proj-bio"],
            "project_names": ["Alpha", "Bio"],
        }
    ]


def test_create_principal():
    """Test adding a principal to a project via path params."""
    datazone = MagicMock()

    with patch.object(control_plane, "datazone_enabled", return_value=True):
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            mock_config_cls.from_env.return_value = _config()
            with patch.object(control_plane, "_datazone_service"):
                response = control_plane.add_principal_to_project(
                    principal="alice",
                    project_id="proj-bio",
                    datazone=datazone,
                )

    assert response["principal"] == "alice"
    assert "datazone_project_id" in response


def test_create_principal_unknown_project_id():
    """Test adding a principal with an unknown project_id returns 404."""
    datazone = MagicMock()
    with patch.object(control_plane, "datazone_enabled", return_value=True):
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            mock_config_cls.from_env.return_value = _config()
            with patch.object(control_plane, "_datazone_service"):
                with pytest.raises(HTTPException) as exc_info:
                    control_plane.add_principal_to_project(
                        principal="alice",
                        project_id="proj-does-not-exist",
                        datazone=datazone,
                    )

    assert exc_info.value.status_code == 404


def test_delete_principal():
    """Test removing a principal from a specific project via path params."""
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config()
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            response = control_plane.remove_principal_from_project(
                principal="alice",
                project_id="proj-alpha",
                datazone=datazone,
            )

    assert "Removed" in response["message"]
    service.delete_project_membership.assert_called_once()


def test_delete_principal_respects_explicit_project_id():
    """Removing from an explicit project calls delete directly without resolving membership."""
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config()
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            response = control_plane.remove_principal_from_project(
                principal="alice",
                project_id="proj-bio",
                datazone=datazone,
            )

    assert "Removed" in response["message"]
    service.find_project_for_principal.assert_not_called()
    service.delete_project_membership.assert_called_once_with(
        project_id="proj-bio",
        user_identifier="alice",
    )


def test_list_principals_by_project():
    """Test listing members of a specific project."""
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config()
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            service.list_project_members.return_value = ["alice", "bob"]
            response = control_plane.list_principals_by_project(
                project_id="proj-alpha",
                datazone=datazone,
            )

    assert response["project_id"] == "proj-alpha"
    assert response["principals"] == ["alice", "bob"]


def test_list_principals_by_project_unknown_returns_404():
    """Test that an unknown project_id returns 404."""
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config()
        with pytest.raises(HTTPException) as exc_info:
            control_plane.list_principals_by_project(
                project_id="proj-does-not-exist",
                datazone=datazone,
            )

    assert exc_info.value.status_code == 404


def test_list_projects_for_principal():
    """Test listing all projects a principal belongs to."""
    datazone = MagicMock()
    with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
        mock_config_cls.from_env.return_value = _config(include_third=False)
        with patch.object(control_plane, "_datazone_service") as factory:
            service = factory.return_value
            # alice is in proj-alpha but not proj-bio
            service.list_project_members.side_effect = [["alice", "bob"], ["bob"]]
            response = control_plane.list_projects_for_principal(
                principal="alice",
                datazone=datazone,
            )

    assert response["principal"] == "alice"
    assert len(response["projects"]) == 1
    assert response["projects"][0]["project_id"] == "proj-alpha"


def test_list_policies_without_statements():
    datazone = MagicMock()
    response_payload = [
        {
            "listingId": "l1",
            "name": "demo/package-grant",
            "entityType": "QuiltPackage",
            "owningProjectId": "proj-alpha",
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
            "owningProjectId": "proj-alpha",
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
        config = _config()
        config = DataZoneConfig(
            domain_id=config.domain_id,
            projects=config.projects,
            asset_type_name="QuiltPackage",
            asset_type_revision="2",
        )
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
    assert [project["project_name"] for project in response["datazone"]["projects"]] == [
        "slot-a",
        "slot-b",
        "slot-c",
    ]
    assert response["datazone"]["projects"][0]["portal_url"].endswith(
        "/projects/proj-alpha/overview"
    )
    assert response["datazone"]["projects"][0]["environment_id"] == "env-alpha"
    assert response["datazone"]["projects"][0]["environment_url"].endswith(
        "/environments/env-alpha"
    )
    assert response["datazone"]["projects"][1]["portal_url"].endswith("/projects/proj-bio/overview")
    assert response["datazone"]["projects"][1]["environment_id"] == "env-bio"
    assert response["datazone"]["projects"][1]["environment_url"].endswith("/environments/env-bio")
    assert response["datazone"]["projects"][2]["portal_url"].endswith(
        "/projects/proj-compute/overview"
    )
    assert response["datazone"]["projects"][2]["environment_id"] == "env-compute"
    assert response["datazone"]["projects"][2]["environment_url"].endswith(
        "/environments/env-compute"
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
    listing.owner_project_id = "proj-alpha"

    with patch.object(control_plane, "_datazone_service") as factory:
        with patch.object(control_plane, "DataZoneConfig") as mock_config_cls:
            config = _config()
            mock_config_cls.from_env.return_value = DataZoneConfig(
                domain_id=config.domain_id,
                projects=config.projects,
                asset_type_name="QuiltPackage",
            )
            service = factory.return_value
            service.list_package_listings.return_value = [listing]
            service.find_accepted_subscription.return_value = None
            service.list_subscription_requests.return_value = [
                {
                    "id": "sub-1",
                    "status": "ACCEPTED",
                    "subscribedPrincipals": [{"project": {"id": "proj-bio"}}],
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
                            "datazone_project_id": "proj-alpha",
                            "datazone_project_name": "Alpha",
                            "last_token_issued": None,
                        }
                    ],
                    "principal_summary": [
                        {
                            "principal": "alice",
                            "project_ids": ["proj-alpha"],
                            "project_names": ["Alpha"],
                        }
                    ],
                },
            ):
                with patch.dict("os.environ", {"AWS_REGION": "us-east-1"}, clear=False):
                    response = control_plane.get_access_graph(datazone=datazone)

    assert response["principal_summary"] == [
        {
            "principal": "alice",
            "project_ids": ["proj-alpha"],
            "project_names": ["Alpha"],
        }
    ]
    assert response["packages"][0]["owner_project_url"].endswith("/projects/proj-alpha/overview")
    assert response["subscriptions"] == [
        {
            "package_name": "demo/package",
            "owner_project_id": "proj-alpha",
            "owner_project_name": "Alpha",
            "consumer_project_id": "proj-bio",
            "consumer_project_name": "Bio",
            "status": "ACCEPTED",
            "subscription_id": "sub-1",
            "subscription_url": "https://dzd-123.sagemaker.us-east-1.on.aws/projects/proj-alpha/catalog/subscriptionRequests/incoming?status=APPROVED",
        }
    ]


def test_studio_subscription_requests_url_maps_status() -> None:
    result = control_plane._studio_subscription_requests_url(
        portal_url="https://dzd-123.sagemaker.us-east-1.on.aws",
        project_id="proj-alpha",
        status="ACCEPTED",
    )

    assert result == (
        "https://dzd-123.sagemaker.us-east-1.on.aws/projects/proj-alpha/"
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
