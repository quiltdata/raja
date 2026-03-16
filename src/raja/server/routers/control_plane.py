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
from raja.datazone import (
    DataZoneConfig,
    DataZoneError,
    DataZoneService,
    datazone_enabled,
    project_id_for_scopes,
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
    scopes: list[str] = []


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


def _ordered_project_ids(config: DataZoneConfig) -> list[str]:
    """Return non-empty project IDs in privilege order (owner first)."""
    return [
        p for p in [config.owner_project_id, config.users_project_id, config.guests_project_id] if p
    ]


def _scopes_for_project(project_id: str, config: DataZoneConfig) -> list[str]:
    """Derive scopes from which DataZone project the principal belongs to."""
    if project_id == config.owner_project_id:
        raw = os.environ.get("RAJA_OWNER_SCOPES", "*:*:*")
    elif project_id == config.users_project_id:
        raw = os.environ.get("RAJA_USERS_SCOPES", "S3Object:*:*")
    else:
        raw = os.environ.get("RAJA_GUESTS_SCOPES", "S3Object:*:s3:GetObject")
    return [s.strip() for s in raw.split(",") if s.strip()]


def _project_name(project_id: str, config: DataZoneConfig) -> str:
    if project_id == config.owner_project_id:
        return "Owner"
    if project_id == config.users_project_id:
        return "Users"
    if project_id == config.guests_project_id:
        return "Guests"
    return project_id


def _derive_principal_scopes(datazone_client: Any, principal: str) -> list[str] | None:
    """Look up which DataZone project the principal belongs to and return scopes.

    Returns None if principal is not a member of any configured project.
    """
    try:
        config = DataZoneConfig.from_env()
        service = _datazone_service(datazone_client)
        project_id = service.find_project_for_principal(
            principal, project_ids=_ordered_project_ids(config)
        )
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    if project_id is None:
        return None
    return _scopes_for_project(project_id, config)


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


def _console_domain_url(*, region: str, domain_id: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/datazone/home"
        f"?region={region}#/domains/{domain_id}"
    )


def _console_listing_url(*, region: str, domain_id: str, listing_id: str) -> str:
    return (
        f"https://{region}.console.aws.amazon.com/datazone/home"
        f"?region={region}#/domains/{domain_id}/browse/{listing_id}"
    )


def _probe_endpoint(
    url: str,
    *,
    ready_path: str | None = None,
) -> dict[str, Any]:
    target = url.rstrip("/")
    if ready_path:
        target = f"{target}{ready_path}"
    try:
        response = httpx.get(target, timeout=5.0, follow_redirects=False)
    except httpx.RequestError as exc:
        return {"reachable": False, "status": "error", "detail": str(exc), "url": target}

    ok = response.status_code < 500
    return {
        "reachable": ok,
        "status": "ok" if ok else "error",
        "status_code": response.status_code,
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
    return sorted(str(name) for name in quilt3.list_packages(registry=registry))


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
        scopes=["*:*:*"],
        ttl=60,
        secret=old_secret,
    )
    new_token = create_token(
        subject="rotation-probe",
        scopes=["*:*:*"],
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

    scopes = _derive_principal_scopes(datazone, payload.principal)
    if scopes is None:
        logger.warning("token_request_principal_not_found", principal=payload.principal)
        raise HTTPException(status_code=404, detail=f"Principal not found: {payload.principal}")

    token_type = payload.token_type.lower()

    if token_type == "rajee":
        issuer = str(request.base_url).rstrip("/")
        token = create_token(
            subject=payload.principal,
            scopes=scopes,
            ttl=TOKEN_TTL,
            secret=secret,
            issuer=issuer,
            audience=["raja-s3-proxy"],
        )
    elif token_type == "raja":
        token = create_token(
            subject=payload.principal,
            scopes=scopes,
            ttl=TOKEN_TTL,
            secret=secret,
        )
    else:
        raise HTTPException(status_code=400, detail=f"Unsupported token_type: {payload.token_type}")

    logger.info(
        "token_issued",
        principal=payload.principal,
        scopes_count=len(scopes),
        ttl=TOKEN_TTL,
    )

    return {"token": token, "principal": payload.principal, "scopes": scopes}


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
            scopes = _scopes_for_project(project_id, config)
            for user_id in members:
                if limit and len(principals) >= limit:
                    break
                principals.append(
                    {
                        "principal": user_id,
                        "datazone_project_id": project_id,
                        "datazone_project_name": _project_name(project_id, config),
                        "scopes": scopes,
                        "scope_count": len(scopes),
                        "last_token_issued": None,
                    }
                )
            if limit and len(principals) >= limit:
                break
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("principals_listed", count=len(principals))
    return {"principals": principals}


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
    domain_status = "warn"
    try:
        domain = datazone.get_domain(identifier=config.domain_id)
        domain_name = str(domain.get("name") or config.domain_id)
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
                "portal_url": _console_domain_url(region=region, domain_id=config.domain_id)
                if region
                else "",
                "status": domain_status,
            },
            "owner_project": {
                "name": _project_name(config.owner_project_id, config),
                "id": config.owner_project_id,
                "status": "ok" if config.owner_project_id else "warn",
            },
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
                    _probe_endpoint(runtime["rale_authorizer_url"])
                    if runtime["rale_authorizer_url"]
                    else {"reachable": False, "status": "warn", "detail": "not configured"}
                ),
            },
            "rale_router": {
                "label": "RALE Router",
                "url": runtime["rale_router_url"],
                "health": (
                    _probe_endpoint(runtime["rale_router_url"])
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
    }


@router.get("/admin/access-graph")
def get_access_graph(
    principal: str | None = Query(default=None),
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, Any]:
    try:
        config = DataZoneConfig.from_env()
        service = _datazone_service(datazone)
        principals_response = list_principals(limit=None, datazone=datazone)
        principals = principals_response["principals"]
        if principal:
            principals = [item for item in principals if item.get("principal") == principal]
        listings = service.list_package_listings()
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or ""
    packages: list[dict[str, Any]] = []
    access_rows: list[dict[str, Any]] = []
    project_filter_ids = {str(item["datazone_project_id"]) for item in principals}

    for listing in listings:
        try:
            subscriptions = 0
            for project_id in _ordered_project_ids(config):
                if project_id == listing.owner_project_id:
                    continue
                accepted = service.find_accepted_subscription(
                    project_id=project_id,
                    listing_id=listing.listing_id,
                )
                if accepted:
                    subscriptions += 1
        except DataZoneError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc

        packages.append(
            {
                "listing_id": listing.listing_id,
                "package_name": listing.name,
                "owner_project_id": listing.owner_project_id,
                "owner_project_name": _project_name(listing.owner_project_id, config),
                "asset_type": config.asset_type_name,
                "subscriptions": subscriptions,
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

    return {
        "principals": principals,
        "packages": packages,
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
    try:
        response = httpx.get(
            target_url,
            headers={"x-raja-principal": payload.principal},
            timeout=20.0,
        )
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
    try:
        response = httpx.get(target_url, headers={"x-rale-taj": payload.taj}, timeout=20.0)
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
    """Add a principal to the appropriate DataZone project based on their scopes."""
    logger.info(
        "principal_create_requested",
        principal=request.principal,
        scopes_count=len(request.scopes),
    )
    if not datazone_enabled():
        raise HTTPException(status_code=503, detail="DataZone is not configured")

    config = DataZoneConfig.from_env()
    project_id = project_id_for_scopes(request.scopes, config)
    if not project_id:
        raise HTTPException(status_code=400, detail="No DataZone project configured for scopes")

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
        "scopes": _scopes_for_project(project_id, config),
        "datazone_project_id": project_id,
    }


@router.delete("/principals/{principal}")
def delete_principal(
    principal: str,
    _: None = Depends(dependencies.require_admin_auth),
    datazone: Any = Depends(dependencies.get_datazone_client),
) -> dict[str, str]:
    """Remove a principal from their DataZone project."""
    logger.info("principal_delete_requested", principal=principal)
    try:
        config = DataZoneConfig.from_env()
        service = _datazone_service(datazone)
        project_id = service.find_project_for_principal(
            principal, project_ids=_ordered_project_ids(config)
        )
        if project_id is None:
            raise HTTPException(status_code=404, detail=f"Principal not found: {principal}")
        service.delete_project_membership(project_id=project_id, user_identifier=principal)
    except DataZoneError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    logger.info("principal_deleted", principal=principal)
    return {"message": f"Principal {principal} deleted"}


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
