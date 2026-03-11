from __future__ import annotations

import json
import os
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
    paths (``/registry/author/pkg/key``). For un-pinned paths the
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


def _resolve_latest_hash_via_quilt3(registry: str, package_name: str) -> str:
    os.environ.setdefault("HOME", "/tmp")
    os.environ.setdefault("XDG_DATA_HOME", "/tmp")
    os.environ.setdefault("XDG_CACHE_HOME", "/tmp")
    try:
        import quilt3  # type: ignore[import-not-found]
    except Exception as exc:  # pragma: no cover - exercised via integration tests
        raise RuntimeError("quilt3 is required for package resolution") from exc
    package = quilt3.Package.browse(
        name=package_name,
        registry=f"s3://{registry}",
    )
    return str(package.top_hash)


def _build_entity_reference(entity: str) -> dict[str, str]:
    if "::" in entity:
        entity_type, entity_id = entity.split("::", 1)
        if entity_type and entity_id:
            return {"entityType": entity_type, "entityId": entity_id}
    return {"entityType": "Raja::User", "entityId": entity}


def _build_package_uri(storage: str, registry: str, package_name: str, manifest_hash: str) -> str:
    return f"quilt+{storage}://{registry}#package={package_name}@{manifest_hash}"


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    policy_store_id = os.environ.get("POLICY_STORE_ID")
    jwt_secret_arn = os.environ.get("JWT_SECRET_ARN")
    jwt_secret_version = os.environ.get("JWT_SECRET_VERSION")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not policy_store_id or not jwt_secret_arn or not region:
        return _response(500, {"error": "missing required environment variables"})

    try:
        principal = _extract_principal(event)
        raw_path = str(event.get("rawPath") or event.get("path") or "")
        registry, package_name, manifest_hash, _logical_key, prefix = _parse_usl(raw_path)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})

    if manifest_hash is None:
        try:
            manifest_hash = _resolve_latest_hash_via_quilt3(registry, package_name)
        except RuntimeError as exc:
            return _response(502, {"error": f"manifest resolution unavailable: {exc}"})
        except Exception as exc:
            return _response(502, {"error": f"manifest resolution failed: {exc}"})
        prefix = f"{registry}/{package_name}@{manifest_hash}/"

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
        "action": {"actionType": "Raja::Action", "actionId": action},
        "resource": {"entityType": "Raja::Package", "entityId": quilt_uri},
        "entities": {
            "entityList": [
                {
                    "identifier": {"entityType": "Raja::Package", "entityId": quilt_uri},
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
        secret_kwargs: dict[str, str] = {"SecretId": jwt_secret_arn}
        if jwt_secret_version:
            secret_kwargs["VersionId"] = jwt_secret_version
        jwt_secret = secrets.get_secret_value(**secret_kwargs)["SecretString"]
    except (ClientError, BotoCoreError, KeyError):
        return _response(503, {"error": "failed to load jwt secret"})

    token_ttl = int(os.environ.get("TOKEN_TTL", "3600"))
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
    )

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
