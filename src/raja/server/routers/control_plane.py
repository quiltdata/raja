"""Control plane router for policy compilation, token issuance, and principal management."""

from __future__ import annotations

import base64
import os
import time
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from raja import compile_policy, create_token
from raja.server import dependencies
from raja.server.audit import build_audit_item
from raja.server.logging_config import get_logger

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


POLICY_STORE_ID = os.environ.get("POLICY_STORE_ID")
TOKEN_TTL = int(os.environ.get("TOKEN_TTL", "3600"))


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


router = APIRouter(prefix="", tags=["control-plane"])


@router.post("/compile")
def compile_policies(
    request: Request,
    avp: Any = Depends(dependencies.get_avp_client),
    mappings_table: Any = Depends(dependencies.get_mappings_table),
    principal_table: Any = Depends(dependencies.get_principal_table),
    audit_table: Any = Depends(dependencies.get_audit_table),
) -> dict[str, Any]:
    logger.info("policy_compilation_started")
    policy_store_id = _require_env(POLICY_STORE_ID, "POLICY_STORE_ID")

    policies_response = avp.list_policies(policyStoreId=policy_store_id, maxResults=100)
    policies_compiled = 0
    principal_scopes: dict[str, set[str]] = {}

    for policy_item in policies_response.get("policies", []):
        policy_id = policy_item["policyId"]
        policy_response = avp.get_policy(policyStoreId=policy_store_id, policyId=policy_id)
        definition = policy_response.get("definition", {})
        static_def = definition.get("static", {})
        cedar_statement = static_def.get("statement", "")
        if not cedar_statement:
            logger.warning("policy_missing_statement", policy_id=policy_id)
            continue

        try:
            principal_scope_map = compile_policy(cedar_statement)
            logger.debug(
                "policy_compiled",
                policy_id=policy_id,
                principals_count=len(principal_scope_map),
            )
        except Exception as exc:
            logger.error(
                "policy_compilation_failed",
                policy_id=policy_id,
                error=str(exc),
            )
            continue

        for principal, scope_list in principal_scope_map.items():
            updated_at = int(time.time())
            mappings_table.put_item(
                Item={"policy_id": policy_id, "scopes": scope_list, "updated_at": updated_at}
            )
            principal_scopes.setdefault(principal, set()).update(scope_list)

        policies_compiled += 1

    for principal, scopes in principal_scopes.items():
        principal_table.put_item(
            Item={"principal": principal, "scopes": list(scopes), "updated_at": int(time.time())}
        )
        logger.debug("principal_scopes_stored", principal=principal, scopes_count=len(scopes))

    logger.info(
        "policy_compilation_completed",
        policies_compiled=policies_compiled,
        principals_count=len(principal_scopes),
    )

    try:
        audit_table.put_item(
            Item=build_audit_item(
                principal="system",
                action="policy.compile",
                resource=policy_store_id,
                decision="SUCCESS",
                policy_store_id=policy_store_id,
                request_id=_get_request_id(request),
            )
        )
    except Exception as exc:
        logger.warning("audit_log_write_failed", error=str(exc))

    return {
        "message": "Policies compiled successfully",
        "policies_compiled": policies_compiled,
        "principals": len(principal_scopes),
    }


@router.post("/token")
def issue_token(
    request: Request,
    payload: TokenRequest,
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


@router.post("/token/revoke")
def revoke_token(payload: RevokeTokenRequest) -> dict[str, str]:
    """Token revocation endpoint (not currently supported)."""
    logger.info("token_revocation_requested")
    return {"status": "unsupported", "message": "Token revocation is not supported"}


@router.get("/principals")
def list_principals(
    limit: int | None = Query(default=None, ge=1),
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
