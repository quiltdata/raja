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


def _parse_usl(raw_path: str) -> tuple[str, str, str | None, str, str | None]:
    """Parse a USL path.

    Supports pinned paths (``/registry/author/pkg@hash/key``) and un-pinned
    paths (``/registry/author/pkg/key``).  For un-pinned paths the
    manifest_hash and prefix elements are returned as ``None``.

    Quilt package names are always two-level (``author/name``), so an
    un-pinned path is unambiguous: the first three segments are
    ``registry/author/name`` and everything after is the logical key.
    """
    path = raw_path.strip("/")
    parts = [p for p in path.split("/") if p]

    registry = parts[0] if parts else ""

    hash_index = -1
    for idx in range(1, len(parts)):
        if "@" in parts[idx]:
            hash_index = idx
            break

    if hash_index != -1:
        # Pinned: /<registry>/.../<pkg>@<hash>/...
        package_ref = "/".join(parts[1 : hash_index + 1])
        package_name, manifest_hash = package_ref.rsplit("@", 1)
        if not package_name or not manifest_hash:
            raise ValueError("USL package reference must be package@hash")
        logical_key = "/".join(parts[hash_index + 1 :])
        prefix = f"{registry}/{package_name}@{manifest_hash}/"
        return registry, package_name, manifest_hash, logical_key, prefix

    # Un-pinned: /<registry>/<author>/<name>/<logical_key...>
    # Requires at least registry + 2-level package + 1 key segment.
    if len(parts) < 4:
        raise ValueError(
            "Un-pinned USL must have at least 4 segments: /<registry>/<author>/<name>/<key>"
        )
    package_name = f"{parts[1]}/{parts[2]}"
    logical_key = "/".join(parts[3:])
    return registry, package_name, None, logical_key, None


def _resolve_manifest_hash(table: Any, registry: str, package_name: str) -> str:
    """Look up the latest pinned manifest hash for a package.

    Expects an item in the manifest-cache table with the synthetic key
    ``pkg:{registry}/{package_name}`` and a ``latest_hash`` attribute.
    """
    pointer_key = f"pkg:{registry}/{package_name}"
    item = table.get_item(Key={"manifest_hash": pointer_key}).get("Item")
    if not item or not isinstance(item.get("latest_hash"), str):
        raise ValueError(
            f"no latest manifest hash registered for {registry}/{package_name}"
        )
    return str(item["latest_hash"])


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

    ddb = boto3.resource("dynamodb", region_name=region)
    cache_table = ddb.Table(taj_cache_table)
    manifest_cache_table_name = os.environ.get("MANIFEST_CACHE_TABLE")

    if manifest_hash is None:
        # Un-pinned USL: resolve the latest manifest hash from the package registry.
        if not manifest_cache_table_name:
            return _response(500, {"error": "MANIFEST_CACHE_TABLE required for un-pinned USLs"})
        manifest_cache = ddb.Table(manifest_cache_table_name)
        try:
            manifest_hash = _resolve_manifest_hash(manifest_cache, registry, package_name)
        except ValueError as exc:
            return _response(404, {"error": str(exc)})
        except (ClientError, BotoCoreError):
            return _response(503, {"error": "failed to resolve manifest hash"})
        prefix = f"{registry}/{package_name}@{manifest_hash}/"

    cache_key = f"{principal}#{manifest_hash}"

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
    assert prefix is not None  # always set: either from URL or resolved above
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
