from __future__ import annotations

import base64
import json
import os
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from raja.manifest import resolve_package_map
from raja.models import S3Location
from raja.token import (
    TokenExpiredError,
    TokenInvalidError,
    TokenValidationError,
    validate_taj_token,
)


def _response(
    status_code: int,
    body: str | dict[str, Any],
    *,
    headers: dict[str, str] | None = None,
    is_base64: bool = False,
) -> dict[str, Any]:
    payload = body if isinstance(body, str) else json.dumps(body)
    return {
        "statusCode": status_code,
        "headers": headers or {"content-type": "application/json"},
        "body": payload,
        "isBase64Encoded": is_base64,
    }


def _extract_taj(event: dict[str, Any]) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}

    taj = headers.get("x-rale-taj")
    if isinstance(taj, str) and taj.strip():
        return taj.strip()

    auth = headers.get("authorization")
    if isinstance(auth, str) and auth.strip():
        if auth.lower().startswith("bearer "):
            return auth[7:].strip()
        return auth.strip()

    raise ValueError("missing TAJ token")


def _parse_usl(raw_path: str) -> tuple[str, str, str | None, str]:
    """Parse a USL path, returning ``manifest_hash=None`` for un-pinned paths.

    Pinned:    /<registry>/author/pkg@hash/key  → hash from URL
    Un-pinned: /<registry>/author/pkg/key       → hash must come from TAJ
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
        package_ref = "/".join(parts[1 : hash_index + 1])
        package_name, manifest_hash = package_ref.rsplit("@", 1)
        logical_key = "/".join(parts[hash_index + 1 :])
        if not logical_key:
            raise ValueError("USL path missing logical key")
        if not package_name or not manifest_hash:
            raise ValueError("USL package reference must be package@hash")
        return registry, package_name, manifest_hash, logical_key

    # Un-pinned: /<registry>/<author>/<name>/<logical_key...>
    if len(parts) < 4:
        raise ValueError(
            "Un-pinned USL must have at least 4 segments: /<registry>/<author>/<name>/<key>"
        )
    package_name = f"{parts[1]}/{parts[2]}"
    logical_key = "/".join(parts[3:])
    if not logical_key:
        raise ValueError("USL path missing logical key")
    return registry, package_name, None, logical_key


def _build_quilt_uri(storage: str, registry: str, package_name: str, manifest_hash: str) -> str:
    return f"quilt+{storage}://{registry}#package={package_name}@{manifest_hash}"


def _get_targets(entries: dict[str, list[dict[str, str]]], logical_key: str) -> list[S3Location]:
    raw = entries.get(logical_key, [])
    targets: list[S3Location] = []
    for location in raw:
        bucket = location.get("bucket")
        key = location.get("key")
        if isinstance(bucket, str) and isinstance(key, str) and bucket and key:
            targets.append(S3Location(bucket=bucket, key=key))
    return targets


def _proxy_get_or_head(
    method: str,
    s3_client: Any,
    bucket: str,
    key: str,
) -> dict[str, Any]:
    try:
        if method == "HEAD":
            head = s3_client.head_object(Bucket=bucket, Key=key)
            headers = {
                "content-type": str(head.get("ContentType") or "application/octet-stream"),
                "content-length": str(head.get("ContentLength") or "0"),
                "x-rale-source-bucket": bucket,
                "x-rale-source-key": key,
            }
            return _response(200, "", headers=headers)

        obj = s3_client.get_object(Bucket=bucket, Key=key)
        body_bytes = obj["Body"].read()
        headers = {
            "content-type": str(obj.get("ContentType") or "application/octet-stream"),
            "content-length": str(obj.get("ContentLength") or len(body_bytes)),
            "x-rale-source-bucket": bucket,
            "x-rale-source-key": key,
        }
        return _response(
            200, base64.b64encode(body_bytes).decode("utf-8"), headers=headers, is_base64=True
        )
    except s3_client.exceptions.NoSuchKey:
        return _response(404, {"error": "object not found"})
    except (ClientError, BotoCoreError):
        return _response(502, {"error": "failed to fetch object from S3"})


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:  # noqa: ARG001
    raw_path = str(event.get("rawPath") or event.get("path") or "")
    if raw_path.rstrip("/") == "/health":
        return _response(200, {"status": "ok"})

    jwt_secret_arn = os.environ.get("JWT_SECRET_ARN")
    jwt_secret_version = os.environ.get("JWT_SECRET_VERSION")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not jwt_secret_arn or not region:
        return _response(500, {"error": "missing required environment variables"})

    try:
        taj = _extract_taj(event)
        raw_path = str(event.get("rawPath") or event.get("path") or "")
        registry, package_name, manifest_hash, logical_key = _parse_usl(raw_path)
    except ValueError as exc:
        return _response(400, {"error": str(exc)})

    try:
        secrets = boto3.client("secretsmanager", region_name=region)
        secret_kwargs: dict[str, str] = {"SecretId": jwt_secret_arn}
        if jwt_secret_version:
            secret_kwargs["VersionId"] = jwt_secret_version
        jwt_secret = secrets.get_secret_value(**secret_kwargs)["SecretString"]
    except (ClientError, BotoCoreError, KeyError):
        return _response(503, {"error": "failed to load jwt secret"})

    try:
        claims = validate_taj_token(taj, jwt_secret)
    except TokenExpiredError:
        return _response(401, {"error": "expired TAJ"})
    except (TokenInvalidError, TokenValidationError):
        return _response(401, {"error": "invalid TAJ"})

    # For un-pinned USLs the hash comes from the TAJ; for pinned USLs validate
    # the URL hash matches the TAJ.
    if manifest_hash is None:
        manifest_hash = claims.manifest_hash
    elif claims.manifest_hash != manifest_hash:
        return _response(403, {"error": "manifest hash mismatch"})

    if claims.package_name != package_name:
        return _response(403, {"error": "package mismatch"})
    if claims.registry != registry:
        return _response(403, {"error": "registry mismatch"})

    storage = os.environ.get("RALE_STORAGE", "s3")
    quilt_uri = _build_quilt_uri(storage, registry, package_name, manifest_hash)
    try:
        package_map = resolve_package_map(quilt_uri)
    except RuntimeError as exc:
        return _response(502, {"error": f"manifest resolution unavailable: {exc}"})
    except Exception as exc:
        return _response(502, {"error": f"manifest resolution failed: {exc}"})

    entries = {
        logical: [location.model_dump(mode="json") for location in locations]
        for logical, locations in package_map.entries.items()
    }

    targets = _get_targets(entries, logical_key)
    if not targets:
        return _response(403, {"error": "logical key is not part of manifest"})

    target = targets[0]
    method = str(event.get("requestContext", {}).get("http", {}).get("method") or "GET").upper()
    if method not in {"GET", "HEAD"}:
        return _response(405, {"error": "only GET and HEAD are supported"})

    s3_client = boto3.client("s3", region_name=region)
    return _proxy_get_or_head(method, s3_client, target.bucket, target.key)
