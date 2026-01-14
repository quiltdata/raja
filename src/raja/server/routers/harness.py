"""S3 harness router for token minting, verification, and enforcement."""

from __future__ import annotations

import base64
import hashlib
import os
import time
from typing import Any, Literal

import jwt
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, ValidationError, model_validator

from raja.server import dependencies
from raja.server.logging_config import get_logger, mask_token

logger = get_logger(__name__)

HARNESS_ISSUER_DEFAULT = "https://raja.local"
HARNESS_AUDIENCE_DEFAULT = "raja-s3"
HARNESS_TTL_DEFAULT = 300


# Local model definitions to avoid circular import
class S3Resource(BaseModel):
    """S3 resource specification."""

    bucket: str = Field(min_length=1)
    key: str | None = None
    prefix: str | None = None

    @model_validator(mode="after")
    def _validate_selector(self) -> S3Resource:
        has_key = bool(self.key)
        has_prefix = bool(self.prefix)
        if has_key == has_prefix:
            raise ValueError("Provide exactly one of key or prefix")
        return self


class S3MintRequest(BaseModel):
    """Request model for minting S3 tokens."""

    subject: str = Field(min_length=1)
    audience: str = Field(min_length=1)
    action: Literal[
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts",
    ]
    bucket: str = Field(min_length=1)
    key: str | None = None
    prefix: str | None = None
    ttl: int | None = Field(default=None, ge=60)

    @model_validator(mode="after")
    def _validate_resource(self) -> S3MintRequest:
        has_key = bool(self.key)
        has_prefix = bool(self.prefix)
        if has_key == has_prefix:
            raise ValueError("Provide exactly one of key or prefix")
        return self


class S3VerifyRequest(BaseModel):
    """Request model for verifying S3 tokens."""

    token: str = Field(min_length=1)
    audience: str | None = None


class S3EnforceRequest(BaseModel):
    """Request model for enforcing S3 authorization."""

    token: str = Field(min_length=1)
    audience: str | None = None
    bucket: str = Field(min_length=1)
    key: str = Field(min_length=1)
    action: Literal[
        "s3:GetObject",
        "s3:PutObject",
        "s3:ListBucket",
        "s3:ListBucketMultipartUploads",
        "s3:ListMultipartUploadParts",
    ]


def _harness_issuer() -> str:
    return os.environ.get("RAJ_HARNESS_ISSUER", HARNESS_ISSUER_DEFAULT)


def _harness_audience() -> str:
    return os.environ.get("RAJ_HARNESS_AUDIENCE", HARNESS_AUDIENCE_DEFAULT)


def _harness_ttl() -> int:
    return int(os.environ.get("RAJ_HARNESS_TTL", str(HARNESS_TTL_DEFAULT)))


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _secret_kid(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return digest[:16]


def _jwks_for_secret(secret: str) -> dict[str, Any]:
    return {
        "keys": [
            {
                "kty": "oct",
                "kid": _secret_kid(secret),
                "use": "sig",
                "alg": "HS256",
                "k": _b64url(secret.encode("utf-8")),
            }
        ]
    }


def _s3_authority_payload(resource: Any) -> dict[str, str]:  # S3Resource from app.py
    payload = {"bucket": resource.bucket}
    if resource.key is not None:
        payload["key"] = resource.key
    if resource.prefix is not None:
        payload["prefix"] = resource.prefix
    return payload


def _s3_resource_allows(authority: Any, bucket: str, key: str) -> bool:  # S3Resource from app.py
    if authority.bucket != bucket:
        return False
    if authority.key is not None:
        return bool(key == authority.key)
    if authority.prefix is not None:
        return bool(key.startswith(authority.prefix))
    return False


router = APIRouter(prefix="/s3-harness", tags=["s3-harness"])


@router.get("/config")
def s3_harness_config(
    secret: str = Depends(dependencies.get_harness_secret),
) -> dict[str, Any]:
    logger.debug("s3_harness_config_requested")
    return {
        "issuer": _harness_issuer(),
        "audience": _harness_audience(),
        "jwks": _jwks_for_secret(secret),
    }


@router.post("/mint")
def s3_harness_mint(
    request: S3MintRequest,
    secret: str = Depends(dependencies.get_harness_secret),
) -> dict[str, Any]:
    logger.info(
        "s3_token_mint_requested",
        subject=request.subject,
        audience=request.audience,
        action=request.action,
        bucket=request.bucket,
        has_key=request.key is not None,
        has_prefix=request.prefix is not None,
    )

    issued_at = int(time.time())
    ttl = request.ttl if request.ttl is not None else _harness_ttl()
    resource = S3Resource(bucket=request.bucket, key=request.key, prefix=request.prefix)
    payload = {
        "iss": _harness_issuer(),
        "sub": request.subject,
        "aud": request.audience,
        "iat": issued_at,
        "exp": issued_at + ttl,
        "action": request.action,
        "s3": _s3_authority_payload(resource),
    }
    headers = {"kid": _secret_kid(secret), "typ": "RAJ"}
    token = jwt.encode(payload, secret, algorithm="HS256", headers=headers)

    logger.info(
        "s3_token_minted",
        subject=request.subject,
        action=request.action,
        ttl=ttl,
        token_preview=mask_token(token),
    )

    return {"token": token, "claims": payload, "kid": headers["kid"]}


@router.post("/verify")
def s3_harness_verify(
    request: S3VerifyRequest,
    secret: str = Depends(dependencies.get_harness_secret),
) -> dict[str, Any]:
    logger.debug("s3_token_verify_requested", token_preview=mask_token(request.token))
    audience = request.audience or _harness_audience()
    try:
        payload = jwt.decode(
            request.token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=_harness_issuer(),
        )
        logger.info("s3_token_verified", subject=payload.get("sub"), action=payload.get("action"))
    except jwt.ExpiredSignatureError:
        logger.warning("s3_token_expired", token_preview=mask_token(request.token))
        return {"valid": False, "error": "token expired"}
    except jwt.InvalidTokenError as exc:
        logger.warning(
            "s3_token_invalid",
            error=str(exc),
            token_preview=mask_token(request.token),
        )
        return {"valid": False, "error": f"invalid token: {exc}"}

    return {
        "valid": True,
        "payload": payload,
        "header": jwt.get_unverified_header(request.token),
    }


@router.post("/enforce")
def s3_harness_enforce(
    request: S3EnforceRequest,
    secret: str = Depends(dependencies.get_harness_secret),
) -> dict[str, Any]:
    logger.info(
        "s3_enforce_requested",
        bucket=request.bucket,
        key=request.key,
        action=request.action,
        token_preview=mask_token(request.token),
    )

    audience = request.audience or _harness_audience()
    try:
        payload = jwt.decode(
            request.token,
            secret,
            algorithms=["HS256"],
            audience=audience,
            issuer=_harness_issuer(),
        )
    except jwt.ExpiredSignatureError:
        logger.warning("s3_enforce_token_expired", bucket=request.bucket, key=request.key)
        return {"allowed": False, "reason": "token expired", "failed_check": "token"}
    except jwt.InvalidTokenError as exc:
        logger.warning(
            "s3_enforce_token_invalid",
            error=str(exc),
            bucket=request.bucket,
            key=request.key,
        )
        return {
            "allowed": False,
            "reason": f"invalid token: {exc}",
            "failed_check": "token",
        }

    authority_action = payload.get("action")
    authority_resource = payload.get("s3", {})
    try:
        authority = S3Resource(
            bucket=authority_resource.get("bucket", ""),
            key=authority_resource.get("key"),
            prefix=authority_resource.get("prefix"),
        )
    except ValidationError as exc:
        logger.warning("s3_resource_validation_failed", error=str(exc), resource=authority_resource)
        return {"allowed": False, "reason": f"invalid authority: {exc}", "failed_check": "resource"}
    except Exception as exc:
        logger.error(
            "unexpected_s3_resource_error",
            error=str(exc),
            resource=authority_resource,
            exc_info=True,
        )
        return {
            "allowed": False,
            "reason": "internal error validating authority",
            "failed_check": "resource",
        }

    action_matches = authority_action == request.action
    resource_matches = _s3_resource_allows(authority, request.bucket, request.key)

    if action_matches and resource_matches:
        logger.info(
            "s3_enforce_allowed",
            subject=payload.get("sub"),
            bucket=request.bucket,
            key=request.key,
            action=request.action,
        )
        return {
            "allowed": True,
            "reason": "request is within authority",
            "request": {"bucket": request.bucket, "key": request.key, "action": request.action},
            "authority": {"action": authority_action, "s3": authority_resource},
        }

    failed_check = "action" if not action_matches else "resource"
    logger.warning(
        "s3_enforce_denied",
        subject=payload.get("sub"),
        bucket=request.bucket,
        key=request.key,
        action=request.action,
        failed_check=failed_check,
        action_matches=action_matches,
        resource_matches=resource_matches,
    )
    return {
        "allowed": False,
        "reason": "request is outside authority",
        "failed_check": failed_check,
        "request": {"bucket": request.bucket, "key": request.key, "action": request.action},
        "authority": {"action": authority_action, "s3": authority_resource},
    }
