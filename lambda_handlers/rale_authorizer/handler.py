from __future__ import annotations

import json
import os
import time
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from raja.quilt_uri import parse_quilt_uri
from raja.token import create_taj_token


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _extract_principal(event: dict[str, Any]) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    principal = headers.get("x-raja-principal")
    if principal:
        return str(principal)

    payload_raw = headers.get("x-raja-jwt-payload")
    if payload_raw:
        try:
            payload = json.loads(payload_raw)
        except json.JSONDecodeError:
            payload = {}
        subject = payload.get("sub")
        if isinstance(subject, str) and subject.strip():
            return subject

    authorizer = (
        event.get("requestContext", {}).get("authorizer", {}).get("jwt", {}).get("claims", {})
    )
    subject = authorizer.get("sub")
    if isinstance(subject, str) and subject.strip():
        return subject

    raise ValueError("principal is required via x-raja-principal or JWT claims")


def _parse_usl(raw_path: str) -> tuple[str, str, str, str, str]:
    path = raw_path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) < 3:
        raise ValueError("USL path must be /<registry>/<package@hash>/<logical-key>")

    registry = parts[0]

    hash_index = -1
    for idx in range(1, len(parts)):
        if "@" in parts[idx]:
            hash_index = idx
            break

    if hash_index == -1:
        raise ValueError("USL path missing package@hash")

    package_ref = "/".join(parts[1 : hash_index + 1])
    package_name, manifest_hash = package_ref.rsplit("@", 1)
    if not package_name or not manifest_hash:
        raise ValueError("USL package reference must be package@hash")

    logical_key = "/".join(parts[hash_index + 1 :])
    prefix = f"{registry}/{package_name}@{manifest_hash}/"
    return registry, package_name, manifest_hash, logical_key, prefix


def _build_entity_reference(entity: str) -> dict[str, str]:
    if "::" in entity:
        entity_type, entity_id = entity.split("::", 1)
        if entity_type and entity_id:
            return {"entityType": entity_type, "entityId": entity_id}
    return {"entityType": "User", "entityId": entity}


def _build_package_uri(storage: str, registry: str, package_name: str, manifest_hash: str) -> str:
    return f"quilt+{storage}://{registry}#package={package_name}@{manifest_hash}"


def _now() -> int:
    return int(time.time())


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    policy_store_id = os.environ.get("POLICY_STORE_ID")
    taj_cache_table = os.environ.get("TAJ_CACHE_TABLE")
    jwt_secret_arn = os.environ.get("JWT_SECRET_ARN")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not policy_store_id or not taj_cache_table or not jwt_secret_arn or not region:
        return _response(500, {"error": "missing required environment variables"})

    try:
        principal = _extract_principal(event)
        raw_path = str(event.get("rawPath") or event.get("path") or "")
        registry, package_name, manifest_hash, _logical_key, prefix = _parse_usl(raw_path)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})

    cache_key = f"{principal}#{manifest_hash}"

    ddb = boto3.resource("dynamodb", region_name=region)
    cache_table = ddb.Table(taj_cache_table)

    try:
        cached = cache_table.get_item(Key={"cache_key": cache_key}).get("Item")
        if cached and isinstance(cached.get("taj"), str):
            return _response(
                200,
                {
                    "decision": str(cached.get("decision") or "ALLOW"),
                    "token": cached["taj"],
                    "cached": True,
                    "manifest_hash": manifest_hash,
                    "package_name": package_name,
                    "registry": registry,
                },
            )
    except (ClientError, BotoCoreError):
        return _response(503, {"error": "failed to read TAJ cache"})

    action = os.environ.get("RALE_ACTION", "quilt:ReadPackage")
    storage = os.environ.get("RALE_STORAGE", "s3")
    quilt_uri = _build_package_uri(storage, registry, package_name, manifest_hash)

    try:
        parse_quilt_uri(quilt_uri)
    except ValueError as exc:
        return _response(400, {"error": f"invalid package URI: {exc}"})

    avp = boto3.client("verifiedpermissions", region_name=region)
    request = {
        "policyStoreId": policy_store_id,
        "principal": _build_entity_reference(principal),
        "action": {"actionType": "Action", "actionId": action},
        "resource": {"entityType": "Package", "entityId": quilt_uri},
        "entities": {
            "entityList": [
                {
                    "identifier": {"entityType": "Package", "entityId": quilt_uri},
                    "attributes": {
                        "registry": {"string": registry},
                        "packageName": {"string": package_name},
                        "hash": {"string": manifest_hash},
                    },
                }
            ]
        },
    }

    try:
        decision = avp.is_authorized(**request).get("decision", "DENY")
    except (ClientError, BotoCoreError):
        return _response(503, {"error": "authorization service unavailable"})

    if decision != "ALLOW":
        return _response(
            403,
            {
                "decision": "DENY",
                "manifest_hash": manifest_hash,
                "package_name": package_name,
                "registry": registry,
            },
        )

    secrets = boto3.client("secretsmanager", region_name=region)
    try:
        jwt_secret = secrets.get_secret_value(SecretId=jwt_secret_arn)["SecretString"]
    except (ClientError, BotoCoreError, KeyError):
        return _response(503, {"error": "failed to load jwt secret"})

    token_ttl = int(os.environ.get("TOKEN_TTL", "3600"))
    issuer = os.environ.get("RALE_ISSUER")
    audience = os.environ.get("RALE_AUDIENCE")
    grants = [f"s3:GetObject/{prefix}"]
    taj = create_taj_token(
        subject=principal,
        grants=grants,
        manifest_hash=manifest_hash,
        package_name=package_name,
        registry=registry,
        ttl=token_ttl,
        secret=jwt_secret,
        issuer=issuer,
        audience=audience,
    )

    cache_ttl_seconds = int(os.environ.get("TAJ_CACHE_TTL_SECONDS", "300"))
    try:
        cache_table.put_item(
            Item={
                "cache_key": cache_key,
                "taj": taj,
                "decision": "ALLOW",
                "ttl": _now() + cache_ttl_seconds,
            }
        )
    except (ClientError, BotoCoreError):
        return _response(503, {"error": "failed to write TAJ cache"})

    return _response(
        200,
        {
            "decision": "ALLOW",
            "token": taj,
            "cached": False,
            "manifest_hash": manifest_hash,
            "package_name": package_name,
            "registry": registry,
        },
    )
