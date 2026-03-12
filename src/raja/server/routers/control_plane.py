"""Control plane router for policy compilation, token issuance, and principal management."""

from __future__ import annotations

import base64
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, model_validator

from raja import create_token
from raja.cedar.entities import parse_entity
from raja.cedar.schema import validate_policy_against_schema
from raja.exceptions import TokenInvalidError
from raja.package_map import parse_s3_path
from raja.quilt_uri import parse_quilt_uri, validate_quilt_uri
from raja.server import dependencies
from raja.server.audit import build_audit_item
from raja.server.logging_config import get_logger
from raja.token import (
    create_token_with_package_grant,
    create_token_with_package_map,
    validate_token,
)

logger = get_logger(__name__)


# Local model definitions to avoid circular import
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


POLICY_STORE_ID = os.environ.get("POLICY_STORE_ID")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))
ROTATION_PK = "ROTATE_SECRET"

# Path to the Cedar schema file for policy validation
_SCHEMA_PATH = Path(__file__).parents[5] / "policies" / "schema.cedar"


def _require_env(value: str | None, name: str) -> str:
    """Ensure environment variable is set."""
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


def _rotation_ttl() -> int:
    return _now() + 7 * 86400


def _put_rotation_operation(
    table: Any,
    *,
    operation_id: str,
    status: str,
    phase: str,
    version_id: str | None = None,
    error: str | None = None,
) -> None:
    item: dict[str, Any] = {
        "pk": ROTATION_PK,
        "event_id": operation_id,
        "status": status,
        "phase": phase,
        "updated_at": _now(),
        "ttl": _rotation_ttl(),
    }
    if version_id:
        item["version_id"] = version_id
    if error:
        item["error"] = error
    table.put_item(Item=item)


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


def _validate_cedar_statement(statement: str) -> None:
    """Validate Cedar statement against schema; raises HTTPException(422) on failure."""
    try:
        validate_policy_against_schema(statement, str(_SCHEMA_PATH), use_cedar_cli=False)
    except (ValueError, FileNotFoundError) as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


router = APIRouter(prefix="", tags=["control-plane"])
_ENTITY_RE = re.compile(r'^(?P<type>.+)::"(?P<id>[^"]+)"$')


def _extract_quilt_uri(resource: str) -> str:
    try:
        resource_type, resource_id = parse_entity(resource)
        if resource_type != "Package":
            raise ValueError("resource must be a Package entity")
        return validate_quilt_uri(resource_id)
    except ValueError:
        return validate_quilt_uri(resource)


def _build_package_entity(quilt_uri: str) -> dict[str, Any]:
    parsed = parse_quilt_uri(quilt_uri)
    return {
        "identifier": {"entityType": "Raja::Package", "entityId": quilt_uri},
        "attributes": {
            "registry": {"string": parsed.registry},
            "packageName": {"string": parsed.package_name},
            "hash": {"string": parsed.hash},
        },
    }


def _build_entity_reference(entity: str) -> dict[str, str]:
    match = _ENTITY_RE.match(entity.strip())
    if match:
        entity_type = match.group("type")
        if "::" not in entity_type:
            entity_type = f"Raja::{entity_type}"
        return {"entityType": entity_type, "entityId": match.group("id")}
    if "::" in entity:
        entity_type, entity_id = entity.split("::", 1)
        if entity_type and entity_id:
            if "::" not in entity_type:
                entity_type = f"Raja::{entity_type}"
            return {"entityType": entity_type, "entityId": entity_id}
    raise ValueError('entity must be in the form Type::"id"')


def _authorize_package(
    avp: Any,
    principal: str,
    action: str,
    quilt_uri: str,
    context: dict[str, Any] | None = None,
) -> bool:
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    request: dict[str, Any] = {
        "policyStoreId": policy_store_id,
        "principal": _build_entity_reference(principal),
        "action": {"actionType": "Raja::Action", "actionId": action},
        "resource": {"entityType": "Raja::Package", "entityId": quilt_uri},
        "entities": {"entityList": [_build_package_entity(quilt_uri)]},
    }
    if context is not None:
        request["context"] = {"contextMap": context}
    response = avp.is_authorized(**request)
    decision: str = response.get("decision", "DENY")
    return decision == "ALLOW"


@router.post("/token")
def issue_token(
    request: Request,
    payload: TokenRequest,
    _: None = Depends(dependencies.require_admin_auth),
    table: Any = Depends(dependencies.get_principal_table),
    audit_table: Any = Depends(dependencies.get_audit_table),
    secret: str = Depends(dependencies.get_jwt_secret),
) -> dict[str, Any]:
    logger.info("token_requested", principal=payload.principal)

    policy_store_id = POLICY_STORE_ID
    response = table.get_item(Key={"principal": payload.principal})
    item = response.get("Item")
    if not item:
        logger.warning("token_request_principal_not_found", principal=payload.principal)
        try:
            audit_table.put_item(
                Item=build_audit_item(
                    principal=payload.principal,
                    action="token.issue",
                    resource=payload.principal,
                    decision="DENY",
                    policy_store_id=policy_store_id,
                    request_id=_get_request_id(request),
                )
            )
        except Exception as exc:
            logger.warning("audit_log_write_failed", error=str(exc))
        raise HTTPException(status_code=404, detail=f"Principal not found: {payload.principal}")

    scopes = item.get("scopes", [])
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

    try:
        audit_table.put_item(
            Item=build_audit_item(
                principal=payload.principal,
                action="token.issue",
                resource=payload.principal,
                decision="SUCCESS",
                policy_store_id=policy_store_id,
                request_id=_get_request_id(request),
            )
        )
    except Exception as exc:
        logger.warning("audit_log_write_failed", error=str(exc))

    return {"token": token, "principal": payload.principal, "scopes": scopes}


@router.post("/token/package")
def issue_package_token(
    request: Request,
    payload: PackageTokenRequest,
    _: None = Depends(dependencies.require_admin_auth),
    avp: Any = Depends(dependencies.get_avp_client),
    audit_table: Any = Depends(dependencies.get_audit_table),
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
        allowed = _authorize_package(
            avp, payload.principal, payload.action, quilt_uri, payload.context
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not allowed:
        try:
            audit_table.put_item(
                Item=build_audit_item(
                    principal=payload.principal,
                    action="token.issue.package",
                    resource=quilt_uri,
                    decision="DENY",
                    policy_store_id=POLICY_STORE_ID,
                    request_id=_get_request_id(request),
                )
            )
        except Exception as exc:
            logger.warning("audit_log_write_failed", error=str(exc))
        raise HTTPException(status_code=403, detail="package access denied")

    token = create_token_with_package_grant(
        subject=payload.principal,
        quilt_uri=quilt_uri,
        mode="read",
        ttl=TOKEN_TTL,
        secret=secret,
    )

    try:
        audit_table.put_item(
            Item=build_audit_item(
                principal=payload.principal,
                action="token.issue.package",
                resource=quilt_uri,
                decision="SUCCESS",
                policy_store_id=POLICY_STORE_ID,
                request_id=_get_request_id(request),
            )
        )
    except Exception as exc:
        logger.warning("audit_log_write_failed", error=str(exc))

    return {"token": token, "principal": payload.principal, "quilt_uri": quilt_uri, "mode": "read"}


@router.post("/token/translation")
def issue_translation_token(
    request: Request,
    payload: TranslationTokenRequest,
    _: None = Depends(dependencies.require_admin_auth),
    avp: Any = Depends(dependencies.get_avp_client),
    audit_table: Any = Depends(dependencies.get_audit_table),
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
        allowed = _authorize_package(
            avp, payload.principal, payload.action, quilt_uri, payload.context
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not allowed:
        try:
            audit_table.put_item(
                Item=build_audit_item(
                    principal=payload.principal,
                    action="token.issue.translation",
                    resource=quilt_uri,
                    decision="DENY",
                    policy_store_id=POLICY_STORE_ID,
                    request_id=_get_request_id(request),
                )
            )
        except Exception as exc:
            logger.warning("audit_log_write_failed", error=str(exc))
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

    try:
        audit_table.put_item(
            Item=build_audit_item(
                principal=payload.principal,
                action="token.issue.translation",
                resource=quilt_uri,
                decision="SUCCESS",
                policy_store_id=POLICY_STORE_ID,
                request_id=_get_request_id(request),
            )
        )
    except Exception as exc:
        logger.warning("audit_log_write_failed", error=str(exc))

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
    audit_table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, str]:
    operation_id = str(uuid.uuid4())
    _put_rotation_operation(
        audit_table,
        operation_id=operation_id,
        status="PENDING",
        phase="starting",
    )
    try:
        _put_rotation_operation(
            audit_table,
            operation_id=operation_id,
            status="PENDING",
            phase="rotating",
        )
        version_id = _perform_secret_rotation()
        _put_rotation_operation(
            audit_table,
            operation_id=operation_id,
            status="SUCCEEDED",
            phase="completed",
            version_id=version_id,
        )
        return {"operation_id": operation_id, "status": "SUCCEEDED"}
    except (RuntimeError, ClientError, BotoCoreError, ValueError) as exc:
        _put_rotation_operation(
            audit_table,
            operation_id=operation_id,
            status="FAILED",
            phase="failed",
            error=str(exc),
        )
        return {"operation_id": operation_id, "status": "FAILED"}


@router.get("/admin/rotate-secret/{operation_id}")
def get_rotate_secret_status(
    operation_id: str,
    _: None = Depends(dependencies.require_admin_auth),
    audit_table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, Any]:
    item = audit_table.get_item(Key={"pk": ROTATION_PK, "event_id": operation_id}).get("Item")
    if not item:
        raise HTTPException(status_code=404, detail="rotation operation not found")
    return {
        "operation_id": operation_id,
        "status": item.get("status", "UNKNOWN"),
        "phase": item.get("phase", "unknown"),
        "version": item.get("version_id"),
        "error": item.get("error") or None,
        "updated_at": item.get("updated_at"),
    }


@router.get("/principals")
def list_principals(
    limit: int | None = Query(default=None, ge=1),
    _: None = Depends(dependencies.require_admin_auth),
    table: Any = Depends(dependencies.get_principal_table),
) -> dict[str, Any]:
    logger.debug("principals_list_requested", limit=limit)
    scan_kwargs: dict[str, Any] = {}
    if limit:
        scan_kwargs["Limit"] = limit
    response = table.scan(**scan_kwargs)
    principals = response.get("Items", [])
    logger.info("principals_listed", count=len(principals))
    return {"principals": principals}


@router.post("/principals")
def create_principal(
    request: PrincipalRequest,
    _: None = Depends(dependencies.require_admin_auth),
    table: Any = Depends(dependencies.get_principal_table),
) -> dict[str, Any]:
    """Create or update principal with scopes."""
    logger.info(
        "principal_create_requested",
        principal=request.principal,
        scopes_count=len(request.scopes),
    )

    table.put_item(
        Item={
            "principal": request.principal,
            "scopes": request.scopes,
            "updated_at": int(time.time()),
        }
    )

    logger.info("principal_created", principal=request.principal)
    return {"principal": request.principal, "scopes": request.scopes}


@router.delete("/principals/{principal}")
def delete_principal(
    principal: str,
    _: None = Depends(dependencies.require_admin_auth),
    table: Any = Depends(dependencies.get_principal_table),
) -> dict[str, str]:
    """Delete a principal and their scopes."""
    logger.info("principal_delete_requested", principal=principal)
    table.delete_item(Key={"principal": principal})
    logger.info("principal_deleted", principal=principal)
    return {"message": f"Principal {principal} deleted"}


@router.get("/policies")
def list_policies(
    include_statements: bool = False,
    _: None = Depends(dependencies.require_admin_auth),
    avp: Any = Depends(dependencies.get_avp_client),
) -> dict[str, Any]:
    logger.debug("policies_list_requested", include_statements=include_statements)
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    response = avp.list_policies(policyStoreId=policy_store_id, maxResults=100)
    policies = response.get("policies", [])

    if not include_statements:
        logger.info("policies_listed", count=len(policies))
        return {"policies": policies}

    detailed: list[dict[str, Any]] = []
    for policy in policies:
        policy_id = policy.get("policyId")
        if not policy_id:
            continue
        policy_response = avp.get_policy(policyStoreId=policy_store_id, policyId=policy_id)
        detailed.append({"policyId": policy_id, "definition": policy_response.get("definition")})

    logger.info("policies_listed_with_statements", count=len(detailed))
    return {"policies": detailed}


@router.post("/policies")
def create_policy(
    request: Request,
    payload: PolicyCreateRequest,
    _: None = Depends(dependencies.require_admin_auth),
    avp: Any = Depends(dependencies.get_avp_client),
    audit_table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, Any]:
    """Create a new Cedar policy in the AVP policy store."""
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    _validate_cedar_statement(payload.statement)
    response = avp.create_policy(
        policyStoreId=policy_store_id,
        definition={"static": {"statement": payload.statement, "description": payload.description}},
    )
    policy_id = response["policyId"]
    logger.info("policy_created", policy_id=policy_id)
    audit_table.put_item(
        Item=build_audit_item(
            principal="admin",
            action="policy.create",
            resource=payload.statement,
            decision="SUCCESS",
            policy_store_id=policy_store_id,
            request_id=_get_request_id(request),
        )
    )
    return {"policyId": policy_id}


@router.get("/policies/{policy_id}")
def get_policy(
    policy_id: str,
    _: None = Depends(dependencies.require_admin_auth),
    avp: Any = Depends(dependencies.get_avp_client),
) -> dict[str, Any]:
    """Get a single Cedar policy with its full statement."""
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    response = avp.get_policy(policyStoreId=policy_store_id, policyId=policy_id)
    logger.debug("policy_fetched", policy_id=policy_id)
    return {"policyId": policy_id, "definition": response.get("definition")}


@router.put("/policies/{policy_id}")
def update_policy(
    policy_id: str,
    request: Request,
    payload: PolicyUpdateRequest,
    _: None = Depends(dependencies.require_admin_auth),
    avp: Any = Depends(dependencies.get_avp_client),
    audit_table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, Any]:
    """Replace the Cedar statement of an existing policy."""
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    _validate_cedar_statement(payload.statement)
    avp.update_policy(
        policyStoreId=policy_store_id,
        policyId=policy_id,
        definition={"static": {"statement": payload.statement}},
    )
    logger.info("policy_updated", policy_id=policy_id)
    audit_table.put_item(
        Item=build_audit_item(
            principal="admin",
            action="policy.update",
            resource=payload.statement,
            decision="SUCCESS",
            policy_store_id=policy_store_id,
            request_id=_get_request_id(request),
        )
    )
    return {"policyId": policy_id, "updated": True}


@router.delete("/policies/{policy_id}")
def delete_policy(
    policy_id: str,
    request: Request,
    _: None = Depends(dependencies.require_admin_auth),
    avp: Any = Depends(dependencies.get_avp_client),
    audit_table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, Any]:
    """Delete a Cedar policy from the AVP policy store."""
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")
    avp.delete_policy(policyStoreId=policy_store_id, policyId=policy_id)
    logger.info("policy_deleted", policy_id=policy_id)
    audit_table.put_item(
        Item=build_audit_item(
            principal="admin",
            action="policy.delete",
            resource=policy_id,
            decision="SUCCESS",
            policy_store_id=policy_store_id,
            request_id=_get_request_id(request),
        )
    )
    return {"policyId": policy_id, "deleted": True}


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
