"""Control plane router for token issuance and principal management."""

from __future__ import annotations

import base64
import os
import re
import secrets
import time
import uuid
from typing import Any
from urllib.parse import quote

import boto3
import httpx
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, model_validator

from raja import create_token
from raja.aws_sigv4 import build_sigv4_request
from raja.datazone import (
    DataZoneConfig,
    DataZoneError,
    DataZoneService,
    datazone_enabled,
)
from raja.exceptions import TokenInvalidError
from raja.manifest import _load_quilt3
from raja.package_map import parse_s3_path
from raja.quilt_uri import QuiltUri, parse_quilt_uri, validate_quilt_uri
from raja.rale.config import resolve_config
from raja.server import dependencies
from raja.server.logging_config import get_logger
from raja.token import (
    create_token_with_package_grant,
    create_token_with_package_map,
    decode_token,
    validate_taj_token,
    validate_token,
)

logger = get_logger(__name__)


class TokenRequest(BaseModel):
    """Request model for token issuance."""

    principal: str
    token_type: str = "raja"


class PrincipalRequest(BaseModel):
    """Request model for principal creation."""

    principal: str
    project_id: str | None = None


class RevokeTokenRequest(BaseModel):
    """Request model for token revocation."""

    token: str


class PackageTokenRequest(BaseModel):
    """Request model for package token issuance."""

    principal: str
    resource: str
    action: str = "quilt:ReadPackage"
    context: dict[str, Any] | None = None


class TranslationTokenRequest(BaseModel):
    """Request model for translation token issuance."""

    principal: str
    resource: str
    action: str = "quilt:ReadPackage"
    logical_bucket: str | None = None
    logical_key: str | None = None
    logical_s3_path: str | None = None
    context: dict[str, Any] | None = None

    @model_validator(mode="after")
    def _validate_logical(self) -> TranslationTokenRequest:
        has_path = bool(self.logical_s3_path)
        has_bucket = bool(self.logical_bucket)
        has_key = bool(self.logical_key)
        if has_path:
            bucket, key = parse_s3_path(str(self.logical_s3_path))
            if has_bucket and self.logical_bucket != bucket:
                raise ValueError("logical_bucket does not match logical_s3_path")
            if has_key and self.logical_key != key:
                raise ValueError("logical_key does not match logical_s3_path")
            self.logical_bucket = bucket
            self.logical_key = key
        if not self.logical_bucket or not self.logical_key:
            raise ValueError("logical_bucket and logical_key are required")
        return self


class PolicyCreateRequest(BaseModel):
    """Request model for policy creation."""

    statement: str
    description: str = ""


class PolicyUpdateRequest(BaseModel):
    """Request model for policy update."""

    statement: str


class RaleAuthorizeRequest(BaseModel):
    principal: str
    usl: str


class RaleDeliverRequest(BaseModel):
    usl: str
    taj: str


DATAZONE_DOMAIN_ID = os.environ.get("DATAZONE_DOMAIN_ID")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))


def _require_env(value: str | None, name: str) -> str:
    if not value:
        raise RuntimeError(f"{name} is required")
    return value


def _get_request_id(request: Request) -> str:
    return (
        request.headers.get("x-request-id")
        or request.headers.get("x-amzn-requestid")
        or str(uuid.uuid4())
    )


def _now() -> int:
    return int(time.time())


def _authorization_plane_id() -> str:
    return f"datazone:{_require_env(DATAZONE_DOMAIN_ID, 'DATAZONE_DOMAIN_ID')}"


def _datazone_service(client: Any) -> DataZoneService:
    return DataZoneService(client=client, config=DataZoneConfig.from_env())


def _ordered_slots(config: DataZoneConfig) -> list[tuple[str, Any]]:
    return config.ordered_slots()


def _ordered_project_ids(config: DataZoneConfig) -> list[str]:
    """Return non-empty configured project IDs in slot order."""
    return [slot.project_id for _, slot in _ordered_slots(config)]


def _project_name(project_id: str, config: DataZoneConfig) -> str:
    slot_name = config.slot_name_for_project(project_id)
    if slot_name is not None:
        slot = config.slot(slot_name)
        if slot.project_label:
            return slot.project_label
    return project_id


def _authorize_package_with_datazone(
    datazone: Any,
    principal: str,
    quilt_uri: str,
) -> bool:
    """Returns True if principal's DataZone project has a grant for the package."""
    try:
        config = DataZoneConfig.from_env()
        service = _datazone_service(datazone)
        project_id = service.find_project_for_principal(
            principal, project_ids=_ordered_project_ids(config)
        )
        if project_id is None:
            raise HTTPException(
                status_code=404,
                detail=f"DataZone project not found for principal: {principal}",
            )
        return service.has_package_grant(project_id=project_id, quilt_uri=quilt_uri)
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _console_lambda_url(*, region: str, fn_name: str) -> str:
    return f"https://console.aws.amazon.com/lambda/home?region={region}#/functions/{fn_name}"


def _console_logs_url(*, region: str, log_group: str) -> str:
    from urllib.parse import quote

    encoded = quote(log_group, safe="").replace("%", "$25")
    return f"https://console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{encoded}"


def _console_secret_url(*, region: str, secret_arn: str) -> str:
    parts = secret_arn.split(":")
    secret_name = parts[6] if len(parts) > 6 else secret_arn
    return (
        f"https://console.aws.amazon.com/secretsmanager/secret?name={secret_name}&region={region}"
    )


def _console_s3_url(*, bucket: str) -> str:
    return f"https://s3.console.aws.amazon.com/s3/buckets/{bucket}"


def _console_ecs_url(*, region: str, cluster: str, service: str) -> str:
    return (
        f"https://console.aws.amazon.com/ecs/v2/clusters/{cluster}"
        f"/services/{service}/health?region={region}"
    )


def _build_console_links(*, request: Request, region: str) -> list[dict[str, str]]:
    """Build AWS Console deep-links from Lambda environment variables."""
    import re as _re

    links: list[dict[str, str]] = []
    if not region:
        return links

    # API Gateway — extract API ID from the request host
    host = request.url.hostname or ""
    m = _re.match(r"([a-z0-9]+)\.execute-api\.", host)
    if m:
        api_id = m.group(1)
        links.append(
            {
                "label": "API Gateway",
                "url": f"https://console.aws.amazon.com/apigateway/home?region={region}#/apis/{api_id}/resources",
            }
        )

    # Control plane Lambda (AWS_LAMBDA_FUNCTION_NAME is auto-injected by the runtime)
    cp_fn = os.environ.get("AWS_LAMBDA_FUNCTION_NAME", "").strip()
    if cp_fn:
        links.append(
            {
                "label": "Control Plane Lambda",
                "url": _console_lambda_url(region=region, fn_name=cp_fn),
                "logs_url": _console_logs_url(region=region, log_group=f"/aws/lambda/{cp_fn}"),
                "group": "Lambda",
            }
        )

    # RALE Authorizer Lambda
    auth_fn = os.environ.get("RALE_AUTHORIZER_FUNCTION_NAME", "").strip()
    if auth_fn:
        links.append(
            {
                "label": "RALE Authorizer Lambda",
                "url": _console_lambda_url(region=region, fn_name=auth_fn),
                "logs_url": _console_logs_url(region=region, log_group=f"/aws/lambda/{auth_fn}"),
                "group": "Lambda",
            }
        )

    # RALE Router Lambda
    router_fn = os.environ.get("RALE_ROUTER_FUNCTION_NAME", "").strip()
    if router_fn:
        links.append(
            {
                "label": "RALE Router Lambda",
                "url": _console_lambda_url(region=region, fn_name=router_fn),
                "logs_url": _console_logs_url(region=region, log_group=f"/aws/lambda/{router_fn}"),
                "group": "Lambda",
            }
        )

    # JWT Signing Secret
    jwt_arn = os.environ.get("JWT_SECRET_ARN", "").strip()
    if jwt_arn and region:
        links.append(
            {"label": "JWT Secret", "url": _console_secret_url(region=region, secret_arn=jwt_arn)}
        )

    # ECS service
    cluster = os.environ.get("ECS_CLUSTER_NAME", "").strip()
    service = os.environ.get("ECS_SERVICE_NAME", "").strip()
    if cluster and service:
        links.append(
            {
                "label": "RAJEE ECS Service",
                "url": _console_ecs_url(region=region, cluster=cluster, service=service),
            }
        )

    # S3 buckets
    registry = os.environ.get("RAJA_REGISTRY", "").strip().removeprefix("s3://")
    if registry:
        links.append(
            {"label": "Registry Bucket", "url": _console_s3_url(bucket=registry), "group": "S3"}
        )

    test_bucket = os.environ.get("RAJEE_TEST_BUCKET_NAME", "").strip()
    if test_bucket:
        links.append(
            {"label": "Test Bucket", "url": _console_s3_url(bucket=test_bucket), "group": "S3"}
        )

    # Sort: group items sort by their group name; ungrouped items sort by label.
    # Both interleave alphabetically (e.g. "Lambda" slots between "JWT Secret" and "RAJEE ECS").
    links.sort(key=lambda x: (x.get("group", x["label"]).casefold(), x["label"].casefold()))
    return links


def _console_domain_url(*, region: str, domain_id: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/datazone/home"
        f"?region={region}#/domains/{domain_id}"
    )


def _console_project_url(*, region: str, domain_id: str, project_id: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/datazone/home"
        f"?region={region}#/domains/{domain_id}/projects/{project_id}"
    )


def _console_environment_url(*, region: str, domain_id: str, environment_id: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/datazone/home"
        f"?region={region}#/domains/{domain_id}/environments/{environment_id}"
    )


def _studio_project_url(*, portal_url: str, project_id: str) -> str:
    return f"{portal_url.rstrip('/')}/projects/{project_id}/overview"


def _studio_subscription_requests_url(
    *,
    portal_url: str,
    project_id: str,
    status: str,
) -> str:
    status_map = {
        "ACCEPTED": "APPROVED",
        "PENDING": "PENDING",
        "REJECTED": "REJECTED",
        "REVOKED": "REVOKED",
    }
    mapped_status = status_map.get(status.upper(), status.upper())
    return (
        f"{portal_url.rstrip('/')}/projects/{project_id}/catalog/subscriptionRequests/incoming"
        f"?status={quote(mapped_status, safe='')}"
    )


def _console_listing_url(*, region: str, domain_id: str, listing_id: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/datazone/home"
        f"?region={region}#/domains/{domain_id}/browse/{listing_id}"
    )


def _project_environment_id(project_id: str, config: DataZoneConfig) -> str:
    slot_name = config.slot_name_for_project(project_id)
    if slot_name is not None:
        return config.slot(slot_name).environment_id
    return ""


def _project_structure(
    *,
    slot_name: str,
    project_id: str,
    config: DataZoneConfig,
    region: str,
    domain_portal_url: str,
) -> dict[str, Any]:
    environment_id = _project_environment_id(project_id, config)
    return {
        "slot_name": slot_name,
        "name": _project_name(project_id, config),
        "id": project_id,
        "portal_url": (
            _studio_project_url(
                portal_url=domain_portal_url,
                project_id=project_id,
            )
            if domain_portal_url and project_id
            else (
                _console_project_url(
                    region=region,
                    domain_id=config.domain_id,
                    project_id=project_id,
                )
                if region and project_id
                else ""
            )
        ),
        "environment_id": environment_id,
        "environment_url": (
            _console_environment_url(
                region=region,
                domain_id=config.domain_id,
                environment_id=environment_id,
            )
            if region and environment_id
            else ""
        ),
        "status": "ok" if project_id else "warn",
    }


def _project_structures(
    *,
    config: DataZoneConfig,
    region: str,
    domain_portal_url: str,
) -> list[dict[str, Any]]:
    return [
        _project_structure(
            slot_name=slot_name,
            project_id=slot.project_id,
            config=config,
            region=region,
            domain_portal_url=domain_portal_url,
        )
        for slot_name, slot in _ordered_slots(config)
    ]


def _summarize_principals(principals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, Any]] = {}
    for item in principals:
        principal = str(item.get("principal") or "")
        if not principal:
            continue
        summary = grouped.setdefault(
            principal,
            {
                "principal": principal,
                "project_ids": [],
                "project_names": [],
            },
        )
        project_id = str(item.get("datazone_project_id") or "")
        project_name = str(item.get("datazone_project_name") or "")
        if project_id and project_id not in summary["project_ids"]:
            summary["project_ids"].append(project_id)
        if project_name and project_name not in summary["project_names"]:
            summary["project_names"].append(project_name)
    return list(grouped.values())


def _probe_endpoint(
    url: str,
    *,
    ready_path: str | None = None,
) -> dict[str, Any]:
    target = url.rstrip("/")
    if ready_path:
        suffix = ready_path if ready_path.startswith("/") else f"/{ready_path}"
        target = f"{target}{suffix}"
    try:
        if ".lambda-url." in target:
            request = build_sigv4_request(method="GET", url=target)
            with httpx.Client(timeout=5.0, follow_redirects=False) as client:
                response = client.send(request)
        else:
            response = httpx.get(target, timeout=5.0, follow_redirects=False)
    except (httpx.RequestError, RuntimeError) as exc:
        return {"reachable": False, "status": "error", "detail": str(exc), "url": target}

    status_code = response.status_code
    if status_code < 400:
        status = "ok"
        reachable = True
    elif status_code < 500:
        status = "warn"
        reachable = True
    else:
        status = "error"
        reachable = False
    return {
        "reachable": reachable,
        "status": status,
        "status_code": status_code,
        "url": target,
    }


def _resolve_runtime_config() -> dict[str, str]:
    resolved, _ = resolve_config()
    authorizer_url = resolved.rale_authorizer_url
    if not authorizer_url:
        authorizer_url = _resolve_lambda_function_url(
            os.environ.get("RALE_AUTHORIZER_FUNCTION_NAME", "").strip()
        )

    router_url = resolved.rale_router_url
    if not router_url:
        router_url = _resolve_lambda_function_url(
            os.environ.get("RALE_ROUTER_FUNCTION_NAME", "").strip()
        )

    return {
        "registry": resolved.registry,
        "rajee_endpoint": resolved.rajee_endpoint,
        "rale_authorizer_url": authorizer_url,
        "rale_router_url": router_url,
    }


def _resolve_lambda_function_url(function_name: str) -> str:
    if not function_name:
        return ""
    try:
        client = boto3.client("lambda")
        response = client.get_function_url_config(FunctionName=function_name)
    except (BotoCoreError, ClientError) as exc:
        logger.warning(
            "lambda_function_url_lookup_failed", function_name=function_name, error=str(exc)
        )
        return ""
    function_url = response.get("FunctionUrl")
    return str(function_url).rstrip("/") if isinstance(function_url, str) else ""


def _external_base_url(request: Request) -> str:
    scheme = request.headers.get("x-forwarded-proto") or request.url.scheme or "https"
    host = request.headers.get("host") or request.url.netloc
    event = request.scope.get("aws.event")
    stage = ""
    if isinstance(event, dict):
        request_context = event.get("requestContext")
        if isinstance(request_context, dict):
            raw_stage = request_context.get("stage")
            if isinstance(raw_stage, str) and raw_stage and raw_stage != "$default":
                stage = raw_stage.strip("/")

    base_url = f"{scheme}://{host}".rstrip("/")
    return f"{base_url}/{stage}" if stage else base_url


def _build_rale_path(usl: str) -> tuple[str, str]:
    parsed = parse_quilt_uri(usl)
    if not parsed.path:
        raise ValueError("USL path is required")
    pinned_path = f"/{parsed.registry}/{parsed.package_name}@{parsed.hash}/{parsed.path}"
    unpinned_path = f"/{parsed.registry}/{parsed.package_name}/{parsed.path}"
    return pinned_path, unpinned_path


def _render_claim_annotation(key: str, value: Any) -> dict[str, Any]:
    explanations = {
        "sub": "Principal the TAJ was minted for.",
        "grants": "Concrete S3 grant prefixes derived from the DataZone decision.",
        "manifest_hash": "Immutable Quilt manifest hash pinned at authorization time.",
        "package_name": "Package name the TAJ is allowed to access.",
        "registry": "Registry bucket backing the package.",
        "iat": "Issue time for the TAJ.",
        "exp": "Expiry time after which the TAJ must be rejected.",
    }
    return {
        "key": key,
        "value": value,
        "explanation": explanations.get(key, "Token claim."),
    }


def _read_registry_packages(registry: str) -> list[str]:
    quilt3 = _load_quilt3()
    try:
        return sorted(str(name) for name in quilt3.list_packages(registry=registry))
    except Exception as exc:
        raise RuntimeError("Cannot list registry packages - check registry access") from exc


def _browse_package_files(registry: str, package_name: str) -> tuple[str, list[dict[str, Any]]]:
    quilt3 = _load_quilt3()
    try:
        package = quilt3.Package.browse(name=package_name, registry=registry)
    except Exception as exc:
        raise RuntimeError("Cannot resolve latest hash - check registry access") from exc

    manifest_hash = getattr(package, "top_hash", None)
    if not isinstance(manifest_hash, str) or not manifest_hash:
        raise RuntimeError("Cannot resolve latest hash - check registry access")

    entries: list[dict[str, Any]] = []
    registry_bucket = registry.split("://", 1)[1] if "://" in registry else registry
    for logical_path, entry in package.walk():
        size_value = getattr(entry, "size", None)
        normalized_path = str(logical_path)
        usl = QuiltUri(
            storage="s3",
            registry=registry_bucket,
            package_name=package_name,
            hash=manifest_hash,
            path=normalized_path,
        ).normalized()
        entries.append(
            {
                "path": normalized_path,
                "size": size_value if isinstance(size_value, int) else None,
                "usl": usl,
            }
        )
    return manifest_hash, entries


router = APIRouter(prefix="", tags=["control-plane"])
_ENTITY_RE = re.compile(r'^(?P<type>.+)::"(?P<id>[^"]+)"$')


def _extract_quilt_uri(resource: str) -> str:
    try:
        resource_type, resource_id = _parse_entity(resource)
        if resource_type != "Package":
            raise ValueError("resource must be a Package entity")
        return validate_quilt_uri(resource_id)
    except ValueError:
        return validate_quilt_uri(resource)


def _parse_entity(entity: str) -> tuple[str, str]:
    match = _ENTITY_RE.match(entity.strip())
    if not match:
        raise ValueError('entity must be in the form Type::"id"')
    return match.group("type"), match.group("id")


def _load_secret(
    secrets_client: Any,
    *,
    secret_arn: str,
    version_id: str | None = None,
) -> str:
    kwargs: dict[str, str] = {"SecretId": secret_arn}
    if version_id:
        kwargs["VersionId"] = version_id
    response = secrets_client.get_secret_value(**kwargs)
    secret_value = response.get("SecretString")
    if not isinstance(secret_value, str) or not secret_value:
        raise RuntimeError("secret value is missing or invalid")
    return secret_value


def _update_function_secret_version(
    lambda_client: Any,
    *,
    function_name: str,
    version_id: str,
) -> None:
    response = lambda_client.get_function_configuration(FunctionName=function_name)
    env_vars = dict(response.get("Environment", {}).get("Variables", {}))
    env_vars["JWT_SECRET_VERSION"] = version_id
    lambda_client.update_function_configuration(
        FunctionName=function_name,
        Environment={"Variables": env_vars},
    )
    waiter_name = "function_updated_v2"
    available_waiters = set(lambda_client.waiter_names)
    if waiter_name not in available_waiters:
        waiter_name = "function_updated"
    lambda_client.get_waiter(waiter_name).wait(FunctionName=function_name)


def _recycle_function(lambda_client: Any, *, function_name: str) -> None:
    previous_reserved: int | None = None
    try:
        current = lambda_client.get_function_concurrency(FunctionName=function_name)
        previous_reserved = current.get("ReservedConcurrentExecutions")
    except ClientError as exc:
        error_code = exc.response.get("Error", {}).get("Code", "")
        if error_code not in {"ResourceNotFoundException", "TooManyRequestsException"}:
            raise
    lambda_client.put_function_concurrency(
        FunctionName=function_name,
        ReservedConcurrentExecutions=0,
    )
    if previous_reserved is None:
        lambda_client.delete_function_concurrency(FunctionName=function_name)
    else:
        lambda_client.put_function_concurrency(
            FunctionName=function_name,
            ReservedConcurrentExecutions=previous_reserved,
        )


def _resolve_rotation_targets() -> list[str]:
    control_plane_name = os.environ.get("AWS_LAMBDA_FUNCTION_NAME") or os.environ.get(
        "CONTROL_PLANE_FUNCTION_NAME"
    )
    authorizer_name = os.environ.get("RALE_AUTHORIZER_FUNCTION_NAME")
    router_name = os.environ.get("RALE_ROUTER_FUNCTION_NAME")
    if not control_plane_name or not authorizer_name or not router_name:
        raise RuntimeError(
            "rotation targets are not configured "
            "(AWS_LAMBDA_FUNCTION_NAME/CONTROL_PLANE_FUNCTION_NAME, "
            "RALE_AUTHORIZER_FUNCTION_NAME, RALE_ROUTER_FUNCTION_NAME)"
        )
    return [control_plane_name, authorizer_name, router_name]


def _run_rotation_probes(old_secret: str, new_secret: str) -> None:
    old_token = create_token(
        subject="rotation-probe",
        scopes=[],
        ttl=60,
        secret=old_secret,
    )
    new_token = create_token(
        subject="rotation-probe",
        scopes=[],
        ttl=60,
        secret=new_secret,
    )
    try:
        validate_token(old_token, new_secret)
    except TokenInvalidError:
        pass
    else:
        raise RuntimeError("old-token probe unexpectedly validated")
    validate_token(new_token, new_secret)


def _perform_secret_rotation() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    secret_arn = os.environ.get("JWT_SECRET_ARN")
    if not region or not secret_arn:
        raise RuntimeError("AWS_REGION and JWT_SECRET_ARN are required")

    target_functions = _resolve_rotation_targets()
    lambda_client = boto3.client("lambda", region_name=region)
    secrets_client = boto3.client("secretsmanager", region_name=region)

    old_version = os.environ.get("JWT_SECRET_VERSION")
    old_secret = _load_secret(secrets_client, secret_arn=secret_arn, version_id=old_version)

    new_secret = secrets.token_urlsafe(48)
    put_response = secrets_client.put_secret_value(SecretId=secret_arn, SecretString=new_secret)
    new_version = put_response.get("VersionId")
    if not isinstance(new_version, str) or not new_version:
        raise RuntimeError("failed to create new secret version")

    for function_name in target_functions:
        _update_function_secret_version(
            lambda_client,
            function_name=function_name,
            version_id=new_version,
        )

    for function_name in target_functions:
        _recycle_function(lambda_client, function_name=function_name)

    _run_rotation_probes(old_secret, new_secret)
    return new_version


@router.post("/token")
def issue_token(
    request: Request,
    payload: TokenRequest,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
    secret: str = Depends(dependencies.get_jwt_secret),
) -> dict[str, Any]:
    logger.info("token_requested", principal=payload.principal)

    try:
        config = DataZoneConfig.from_env()
        service = _datazone_service(datazone)
        project_id = service.find_project_for_principal(
            payload.principal, project_ids=_ordered_project_ids(config)
        )
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    if project_id is None:
        logger.warning("token_request_principal_not_found", principal=payload.principal)
        raise HTTPException(status_code=404, detail=f"Principal not found: {payload.principal}")

    token_type = payload.token_type.lower()

    if token_type == "rajee":
        issuer = str(request.base_url).rstrip("/")
        token = create_token(
            subject=payload.principal,
            scopes=[],
            ttl=TOKEN_TTL,
            secret=secret,
            issuer=issuer,
            audience=["raja-s3-proxy"],
        )
    elif token_type == "raja":
        token = create_token(
            subject=payload.principal,
            scopes=[],
            ttl=TOKEN_TTL,
            secret=secret,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported token_type: {payload.token_type}")

    logger.info(
        "token_issued",
        principal=payload.principal,
        ttl=TOKEN_TTL,
    )

    return {"token": token, "principal": payload.principal}


@router.post("/token/package")
def issue_package_token(
    request: Request,
    payload: PackageTokenRequest,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
    secret: str = Depends(dependencies.get_jwt_secret),
) -> dict[str, Any]:
    logger.info("package_token_requested", principal=payload.principal)

    if payload.action != "quilt:ReadPackage":
        raise HTTPException(status_code=400, detail="quilt:WritePackage is not supported")

    try:
        quilt_uri = _extract_quilt_uri(payload.resource)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        allowed = _authorize_package_with_datazone(datazone, payload.principal, quilt_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not allowed:
        raise HTTPException(status_code=403, detail="package access denied")

    token = create_token_with_package_grant(
        subject=payload.principal,
        quilt_uri=quilt_uri,
        mode="read",
        ttl=TOKEN_TTL,
        secret=secret,
    )

    return {"token": token, "principal": payload.principal, "quilt_uri": quilt_uri, "mode": "read"}


@router.post("/token/translation")
def issue_translation_token(
    request: Request,
    payload: TranslationTokenRequest,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
    secret: str = Depends(dependencies.get_jwt_secret),
) -> dict[str, Any]:
    logger.info("translation_token_requested", principal=payload.principal)

    if payload.action != "quilt:ReadPackage":
        raise HTTPException(status_code=400, detail="quilt:WritePackage is not supported")

    try:
        quilt_uri = _extract_quilt_uri(payload.resource)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    try:
        allowed = _authorize_package_with_datazone(datazone, payload.principal, quilt_uri)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not allowed:
        raise HTTPException(status_code=403, detail="package access denied")

    token = create_token_with_package_map(
        subject=payload.principal,
        quilt_uri=quilt_uri,
        mode="read",
        ttl=TOKEN_TTL,
        secret=secret,
        logical_bucket=payload.logical_bucket,
        logical_key=payload.logical_key,
    )

    return {
        "token": token,
        "principal": payload.principal,
        "quilt_uri": quilt_uri,
        "mode": "read",
        "logical_bucket": payload.logical_bucket,
        "logical_key": payload.logical_key,
    }


@router.post("/token/revoke")
def revoke_token(
    payload: RevokeTokenRequest,
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, str]:
    """Token revocation endpoint (not currently supported)."""
    logger.info("token_revocation_requested")
    return {"status": "unsupported", "message": "Token revocation is not supported"}


@router.post("/admin/rotate-secret", status_code=202)
def rotate_secret(
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, str]:
    operation_id = str(uuid.uuid4())
    try:
        _perform_secret_rotation()
        return {"operation_id": operation_id, "status": "SUCCEEDED"}
    except (RuntimeError, ClientError, BotoCoreError, ValueError) as exc:
        logger.warning("secret_rotation_failed", error=str(exc))
        return {"operation_id": operation_id, "status": "FAILED"}


@router.get("/principals")
def list_principals(
    limit: int | None = Query(default=None, ge=1),
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, Any]:
    logger.debug("principals_list_requested", limit=limit)
    try:
        config = DataZoneConfig.from_env()
        service = _datazone_service(datazone)
        principals: list[dict[str, Any]] = []
        for project_id in _ordered_project_ids(config):
            members = service.list_project_members(project_id)
            for user_id in members:
                if limit and len(principals) >= limit:
                    break
                principals.append(
                    {
                        "principal": user_id,
                        "datazone_project_id": project_id,
                        "datazone_project_name": _project_name(project_id, config),
                        "last_token_issued": None,
                    }
                )
            if limit and len(principals) >= limit:
                break
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("principals_listed", count=len(principals))
    return {
        "principals": principals,
        "principal_summary": _summarize_principals(principals),
    }


@router.get("/admin/structure")
def get_admin_structure(
    request: Request,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
    secret: str = Depends(dependencies.get_jwt_secret),
) -> dict[str, Any]:
    runtime = _resolve_runtime_config()
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    config = DataZoneConfig.from_env()

    domain_name = config.domain_id
    domain_portal_url = ""
    domain_status = "warn"
    try:
        domain = datazone.get_domain(identifier=config.domain_id)
        domain_name = str(domain.get("name") or config.domain_id)
        domain_portal_url = str(domain.get("portalUrl") or "")
        domain_status = "ok"
    except Exception as exc:
        domain_status = f"error: {exc}"

    asset_type_status = "warn"
    try:
        asset_type_response = datazone.get_asset_type(
            domainIdentifier=config.domain_id,
            identifier=config.asset_type_name,
            revision=config.asset_type_revision,
        )
        asset_type_name = str(asset_type_response.get("name") or config.asset_type_name)
        asset_type_revision = str(asset_type_response.get("revision") or config.asset_type_revision)
        asset_type_status = "ok"
    except Exception as exc:
        asset_type_name = config.asset_type_name
        asset_type_revision = config.asset_type_revision
        asset_type_status = f"error: {exc}"

    server_url = _external_base_url(request)
    health_url = f"{server_url}/health"
    jwks_url = f"{server_url}/.well-known/jwks.json"
    jwks = get_jwks(secret=secret)

    return {
        "datazone": {
            "domain": {
                "name": domain_name,
                "id": config.domain_id,
                "region": region,
                "portal_url": (
                    domain_portal_url
                    or (
                        _console_domain_url(region=region, domain_id=config.domain_id)
                        if region
                        else ""
                    )
                ),
                "status": domain_status,
            },
            "projects": _project_structures(
                config=config,
                region=region,
                domain_portal_url=domain_portal_url,
            ),
            "asset_type": {
                "name": asset_type_name,
                "revision": asset_type_revision,
                "status": asset_type_status,
            },
        },
        "stack": {
            "server": {
                "label": "RAJA server",
                "url": server_url,
                "health": _probe_endpoint(health_url),
            },
            "rale_authorizer": {
                "label": "RALE Authorizer",
                "url": runtime["rale_authorizer_url"],
                "health": (
                    _probe_endpoint(runtime["rale_authorizer_url"], ready_path="/health")
                    if runtime["rale_authorizer_url"]
                    else {"reachable": False, "status": "warn", "detail": "not configured"}
                ),
            },
            "rale_router": {
                "label": "RALE Router",
                "url": runtime["rale_router_url"],
                "health": (
                    _probe_endpoint(runtime["rale_router_url"], ready_path="/health")
                    if runtime["rale_router_url"]
                    else {"reachable": False, "status": "warn", "detail": "not configured"}
                ),
            },
            "rajee": {
                "label": "RAJEE",
                "url": runtime["rajee_endpoint"],
                "health": (
                    _probe_endpoint(runtime["rajee_endpoint"], ready_path="/ready")
                    if runtime["rajee_endpoint"]
                    else {"reachable": False, "status": "warn", "detail": "not configured"}
                ),
            },
            "jwks": {
                "label": "JWKS",
                "url": jwks_url,
                "kids": [str(key.get("kid") or "") for key in jwks.get("keys", [])],
                "health": {"reachable": True, "status": "ok"},
            },
        },
        "console_links": _build_console_links(request=request, region=region),
    }


@router.get("/admin/access-graph")
def get_access_graph(
    principal: str | None = Query(default=None),
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, Any]:
    if not isinstance(principal, str):
        principal = None
    try:
        config = DataZoneConfig.from_env()
        service = _datazone_service(datazone)
        principals_response = list_principals(limit=None, datazone=datazone)
        principals = principals_response["principals"]
        principal_summary = principals_response["principal_summary"]
        if principal:
            principals = [item for item in principals if item.get("principal") == principal]
            principal_summary = [
                item for item in principal_summary if item.get("principal") == principal
            ]
        listings = service.list_package_listings()
        domain_portal_url = (
            str(datazone.get_domain(identifier=config.domain_id).get("portalUrl") or "")
            if config.domain_id
            else ""
        )
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    packages: list[dict[str, Any]] = []
    subscriptions: list[dict[str, Any]] = []
    access_rows: list[dict[str, Any]] = []
    project_filter_ids = {str(item["datazone_project_id"]) for item in principals}
    listing_index = {listing.listing_id: listing for listing in listings}

    for listing in listings:
        try:
            subscription_count = 0
            for project_id in _ordered_project_ids(config):
                if project_id == listing.owner_project_id:
                    continue
                accepted = service.find_accepted_subscription(
                    project_id=project_id,
                    listing_id=listing.listing_id,
                )
                if accepted:
                    subscription_count += 1
        except DataZoneError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        packages.append(
            {
                "listing_id": listing.listing_id,
                "package_name": listing.name,
                "owner_project_id": listing.owner_project_id,
                "owner_project_name": _project_name(listing.owner_project_id, config),
                "asset_type": config.asset_type_name,
                "subscriptions": subscription_count,
                "owner_project_url": (
                    _studio_project_url(
                        portal_url=domain_portal_url,
                        project_id=listing.owner_project_id,
                    )
                    if domain_portal_url and listing.owner_project_id
                    else (
                        _console_project_url(
                            region=region,
                            domain_id=config.domain_id,
                            project_id=listing.owner_project_id,
                        )
                        if region and listing.owner_project_id
                        else ""
                    )
                ),
                "listing_url": (
                    _console_listing_url(
                        region=region,
                        domain_id=config.domain_id,
                        listing_id=listing.listing_id,
                    )
                    if region
                    else ""
                ),
            }
        )

        for project_id in _ordered_project_ids(config):
            if project_filter_ids and project_id not in project_filter_ids:
                continue
            if project_id == listing.owner_project_id:
                access_rows.append(
                    {
                        "principal_project_id": project_id,
                        "principal_project_name": _project_name(project_id, config),
                        "package_name": listing.name,
                        "access_mode": "OWNED",
                        "source": "listing owner",
                        "subscription_id": None,
                    }
                )
                continue
            try:
                subscription = service.find_accepted_subscription(
                    project_id=project_id,
                    listing_id=listing.listing_id,
                )
            except DataZoneError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            if subscription is None:
                continue
            access_rows.append(
                {
                    "principal_project_id": project_id,
                    "principal_project_name": _project_name(project_id, config),
                    "package_name": listing.name,
                    "access_mode": "GRANTED",
                    "source": "accepted subscription",
                    "subscription_id": str(subscription.get("id") or ""),
                }
            )

    try:
        subscription_requests = service.list_subscription_requests(
            listing_ids=[listing.listing_id for listing in listings]
        )
    except DataZoneError as exc:
        logger.warning("access_graph_subscription_requests_unavailable", error=str(exc))
        subscription_requests = []

    for item in subscription_requests:
        request_id = str(item.get("id") or "")
        status = str(item.get("status") or "UNKNOWN")
        principals_data = item.get("subscribedPrincipals", [])
        consumer_project_id = ""
        if isinstance(principals_data, list):
            for principal_data in principals_data:
                if not isinstance(principal_data, dict):
                    continue
                project_data = principal_data.get("project")
                if isinstance(project_data, dict) and project_data.get("id"):
                    consumer_project_id = str(project_data["id"])
                    break

        listings_data = item.get("subscribedListings", [])
        listing_id = ""
        if isinstance(listings_data, list):
            for listing_data in listings_data:
                if not isinstance(listing_data, dict):
                    continue
                if listing_data.get("id"):
                    listing_id = str(listing_data["id"])
                    break

        matched_listing: Any | None = listing_index.get(listing_id)
        package_name = matched_listing.name if matched_listing else listing_id
        owner_project_id = matched_listing.owner_project_id if matched_listing else ""
        subscriptions.append(
            {
                "package_name": package_name,
                "owner_project_id": owner_project_id,
                "owner_project_name": _project_name(owner_project_id, config)
                if owner_project_id
                else "",
                "consumer_project_id": consumer_project_id,
                "consumer_project_name": _project_name(consumer_project_id, config)
                if consumer_project_id
                else "",
                "status": status,
                "subscription_id": request_id,
                "subscription_url": (
                    _studio_subscription_requests_url(
                        portal_url=domain_portal_url,
                        project_id=owner_project_id,
                        status=status,
                    )
                    if domain_portal_url and owner_project_id
                    else ""
                ),
            }
        )

    subscriptions.sort(
        key=lambda item: (
            str(item.get("package_name") or ""),
            str(item.get("owner_project_name") or ""),
            str(item.get("consumer_project_name") or ""),
            str(item.get("status") or ""),
            str(item.get("subscription_id") or ""),
        )
    )

    return {
        "principals": principals,
        "principal_summary": principal_summary,
        "packages": packages,
        "subscriptions": subscriptions,
        "access": access_rows,
    }


@router.get("/admin/rale/packages")
def list_rale_packages(
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, Any]:
    runtime = _resolve_runtime_config()
    registry = runtime["registry"]
    if not registry:
        raise HTTPException(status_code=503, detail="RAJA_REGISTRY is not configured")
    try:
        packages = _read_registry_packages(registry)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {"registry": registry, "packages": packages}


@router.get("/admin/rale/package-files")
def list_rale_package_files(
    package_name: str = Query(..., min_length=1),
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, Any]:
    runtime = _resolve_runtime_config()
    registry = runtime["registry"]
    if not registry:
        raise HTTPException(status_code=503, detail="RAJA_REGISTRY is not configured")
    try:
        manifest_hash, files = _browse_package_files(registry, package_name)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    return {
        "registry": registry,
        "package_name": package_name,
        "manifest_hash": manifest_hash,
        "files": files,
    }


@router.post("/admin/rale/authorize")
def authorize_rale_request(
    payload: RaleAuthorizeRequest,
    _: None = Depends(dependencies.require_admin_auth),
    secret: str = Depends(dependencies.get_jwt_secret),
) -> dict[str, Any]:
    runtime = _resolve_runtime_config()
    authorizer_url = runtime["rale_authorizer_url"]
    if not authorizer_url:
        raise HTTPException(status_code=503, detail="RALE_AUTHORIZER_URL is not configured")
    try:
        pinned_path, _unpinned_path = _build_rale_path(payload.usl)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_url = f"{authorizer_url.rstrip('/')}{quote(pinned_path, safe='/@')}"
    request = build_sigv4_request(
        method="GET",
        url=target_url,
        headers={"x-raja-principal": payload.principal},
    )
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.send(request)
        body = response.json()
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"RALE authorizer unreachable: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=502,
            detail="RALE authorizer returned invalid JSON",
        ) from exc

    result: dict[str, Any] = {
        "status_code": response.status_code,
        "url": target_url,
        "principal": payload.principal,
        "usl": payload.usl,
        "body": body,
    }
    token_value = body.get("token")
    if response.status_code == 200 and isinstance(token_value, str):
        claims = decode_token(token_value)
        validate_taj_token(token_value, secret)
        annotated = [
            _render_claim_annotation(key, claims.get(key))
            for key in ["sub", "grants", "manifest_hash", "package_name", "registry", "iat", "exp"]
        ]
        result["claims"] = claims
        result["annotated_claims"] = annotated
    return result


@router.post("/admin/rale/deliver")
def deliver_rale_request(
    payload: RaleDeliverRequest,
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, Any]:
    runtime = _resolve_runtime_config()
    router_url = runtime["rale_router_url"]
    if not router_url:
        raise HTTPException(status_code=503, detail="RALE_ROUTER_URL is not configured")
    try:
        pinned_path, _unpinned_path = _build_rale_path(payload.usl)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    target_url = f"{router_url.rstrip('/')}{quote(pinned_path, safe='/@')}"
    request = build_sigv4_request(
        method="GET",
        url=target_url,
        headers={"x-rale-taj": payload.taj},
    )
    try:
        with httpx.Client(timeout=20.0) as client:
            response = client.send(request)
    except httpx.RequestError as exc:
        raise HTTPException(status_code=502, detail=f"RALE router unreachable: {exc}") from exc

    content_type = response.headers.get("content-type", "")
    preview = response.text[:500] if "text" in content_type or "json" in content_type else ""
    try:
        diagnostics = response.json() if "json" in content_type else {}
    except ValueError:
        diagnostics = {}
    return {
        "status_code": response.status_code,
        "url": target_url,
        "byte_count": len(response.content),
        "content_type": content_type,
        "preview": preview,
        "diagnostics": diagnostics,
        "headers": dict(response.headers.items()),
    }


@router.post("/principals")
def create_principal(
    request: PrincipalRequest,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, Any]:
    """Add a principal to the requested DataZone project."""
    logger.info(
        "principal_create_requested",
        principal=request.principal,
    )
    if not datazone_enabled():
        raise HTTPException(status_code=503, detail="DataZone is not configured")

    config = DataZoneConfig.from_env()
    project_id = request.project_id or ""
    if not project_id:
        raise HTTPException(status_code=400, detail="project_id is required")
    if project_id not in _ordered_project_ids(config):
        raise HTTPException(status_code=400, detail=f"Unknown DataZone project: {project_id}")

    try:
        service = _datazone_service(datazone)
        service.ensure_project_membership(
            project_id=project_id,
            user_identifier=request.principal,
        )
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    logger.info("principal_created", principal=request.principal)
    return {
        "principal": request.principal,
        "project_id": project_id,
        "datazone_project_id": project_id,
    }


@router.delete("/principals/{principal}")
def delete_principal(
    principal: str,
    project_id: str | None = Query(default=None),
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, str]:
    """Remove a principal from a DataZone project."""
    if not isinstance(project_id, str):
        project_id = None
    logger.info("principal_delete_requested", principal=principal, project_id=project_id)
    try:
        service = _datazone_service(datazone)
        target_project_id = project_id
        if target_project_id is None:
            config = DataZoneConfig.from_env()
            target_project_id = service.find_project_for_principal(
                principal, project_ids=_ordered_project_ids(config)
            )
        if target_project_id is None:
            raise HTTPException(status_code=404, detail=f"Principal not found: {principal}")
        service.delete_project_membership(project_id=target_project_id, user_identifier=principal)
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("principal_deleted", principal=principal)
    return {"message": f"Removed {principal} from project"}


@router.get("/policies")
def list_policies(
    include_statements: bool = False,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, Any]:
    logger.debug("policies_list_requested", include_statements=include_statements)
    try:
        service = _datazone_service(datazone)
        listings = service._search_listings("")
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    policies: list[dict[str, Any]] = []
    for listing in listings:
        if listing.get("entityType") != service._config.asset_type_name:
            continue
        entry: dict[str, Any] = {
            "policyId": listing.get("listingId"),
            "name": listing.get("name"),
            "ownerProjectId": listing.get("owningProjectId"),
            "type": "datazone-listing",
        }
        if include_statements:
            entry["definition"] = {
                "static": {
                    "statement": "",
                    "description": "DataZone-backed package access listing",
                }
            }
        policies.append(entry)
    logger.info("policies_listed", count=len(policies))
    return {"policies": policies}


@router.post("/policies")
def create_policy(
    request: Request,
    payload: PolicyCreateRequest,
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, Any]:
    """Policy mutation is disabled in the DataZone POC."""
    raise HTTPException(
        status_code=410,
        detail="Direct listing mutation is not supported; manage package grants through DataZone",
    )


@router.get("/policies/{policy_id}")
def get_policy(
    policy_id: str,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, Any]:
    """Get a single DataZone-backed listing."""
    response = list_policies(include_statements=True, datazone=datazone)
    for policy in response["policies"]:
        if isinstance(policy, dict) and policy.get("policyId") == policy_id:
            logger.debug("policy_fetched", policy_id=policy_id)
            return policy
    raise HTTPException(status_code=404, detail="listing not found")


@router.put("/policies/{policy_id}")
def update_policy(
    policy_id: str,
    request: Request,
    payload: PolicyUpdateRequest,
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, Any]:
    """Policy mutation is disabled in the DataZone POC."""
    raise HTTPException(
        status_code=410,
        detail="Direct listing mutation is not supported; manage package grants through DataZone",
    )


@router.delete("/policies/{policy_id}")
def delete_policy(
    policy_id: str,
    request: Request,
    _: None = Depends(dependencies.require_admin_auth),
) -> dict[str, Any]:
    """Policy mutation is disabled in the DataZone POC."""
    raise HTTPException(
        status_code=410,
        detail="Direct listing mutation is not supported; manage package grants through DataZone",
    )


@router.get("/.well-known/jwks.json")
def get_jwks(secret: str = Depends(dependencies.get_jwt_secret)) -> dict[str, Any]:
    """Return JWKS for JWT signature verification."""
    key_bytes = secret.encode("utf-8")
    k_value = base64.urlsafe_b64encode(key_bytes).decode("utf-8").rstrip("=")
    return {
        "keys": [
            {
                "kty": "oct",
                "kid": "raja-jwt-key",
                "alg": "HS256",
                "k": k_value,
            }
        ]
    }
