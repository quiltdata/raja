from __future__ import annotations

import json
import os
import tempfile
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from raja.datazone import DataZoneConfig, DataZoneError, DataZoneService
from raja.quilt_uri import parse_quilt_uri
from raja.token import create_taj_token

__all__ = ["handler"]


def _response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body),
    }


def _normalize_iam_principal(principal: str) -> str:
    value = principal.strip()
    if not value.startswith("arn:aws:sts::"):
        return value

    arn_prefix, separator, resource = value.rpartition(":")
    if not separator:
        return value
    prefix_parts = arn_prefix.split(":")
    if len(prefix_parts) < 5:
        return value
    service = prefix_parts[2]
    account_id = prefix_parts[4]
    if service != "sts" or not resource.startswith("assumed-role/"):
        return value

    role_parts = resource.split("/")
    if len(role_parts) < 3:
        return value
    role_name = "/".join(role_parts[1:-1])
    if not role_name:
        return value
    return f"arn:aws:iam::{account_id}:role/{role_name}"


def _trusted_forwarder_arns() -> set[str]:
    raw = os.environ.get("RAJA_TRUSTED_FORWARDER_ARNS", "")
    return {
        _normalize_iam_principal(value)
        for value in (item.strip() for item in raw.split(","))
        if value
    }


def _allow_asserted_principal() -> bool:
    return os.environ.get("RAJA_ALLOW_ASSERTED_PRINCIPAL", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _extract_principal(event: dict[str, Any]) -> str:
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    request_context = event.get("requestContext", {})
    iam = request_context.get("authorizer", {}).get("iam", {})

    caller_arn = ""
    user_arn = iam.get("userArn")
    if isinstance(user_arn, str) and user_arn.strip():
        caller_arn = _normalize_iam_principal(user_arn)
    caller_user_id = iam.get("userId")
    trusted_forwarder = caller_arn in _trusted_forwarder_arns() if caller_arn else False

    payload_raw = headers.get("x-raja-jwt-payload")
    if payload_raw and trusted_forwarder:
        import base64

        payload_str = payload_raw
        if not payload_str.startswith("{"):
            # Envoy forwards the JWT payload as base64url; decode it first.
            padded = payload_str + "=" * (-len(payload_str) % 4)
            try:
                payload_str = base64.urlsafe_b64decode(padded).decode("utf-8")
            except Exception:
                payload_str = payload_raw
        try:
            payload = json.loads(payload_str)
        except json.JSONDecodeError:
            payload = {}
        subject = payload.get("sub")
        if isinstance(subject, str) and subject.strip():
            return subject

    principal = headers.get("x-raja-principal")
    if trusted_forwarder and principal:
        return str(principal)

    if caller_arn:
        return caller_arn
    if isinstance(caller_user_id, str) and caller_user_id.strip():
        return caller_user_id

    authorizer = request_context.get("authorizer", {}).get("jwt", {}).get("claims", {})
    subject = authorizer.get("sub")
    if isinstance(subject, str) and subject.strip():
        return subject

    if _allow_asserted_principal() and principal:
        return str(principal)

    raise ValueError("principal is required via IAM identity or trusted forwarded identity")


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
    temp_dir = tempfile.gettempdir()
    os.environ.setdefault("HOME", temp_dir)
    os.environ.setdefault("XDG_DATA_HOME", temp_dir)
    os.environ.setdefault("XDG_CACHE_HOME", temp_dir)
    try:
        import quilt3  # type: ignore[import-untyped]
    except Exception as exc:  # pragma: no cover - exercised via integration tests
        raise RuntimeError("quilt3 is required for package resolution") from exc
    package = quilt3.Package.browse(
        name=package_name,
        registry=f"s3://{registry}",
    )
    return str(package.top_hash)


def _build_package_uri(storage: str, registry: str, package_name: str, manifest_hash: str) -> str:
    return f"quilt+{storage}://{registry}#package={package_name}@{manifest_hash}"


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

    storage = os.environ.get("RALE_STORAGE", "s3")
    quilt_uri = _build_package_uri(storage, registry, package_name, manifest_hash)

    try:
        parse_quilt_uri(quilt_uri)
    except ValueError as exc:
        return _response(400, {"error": f"invalid package URI: {exc}"})

    try:
        config = DataZoneConfig.from_env()
        service = DataZoneService(
            client=boto3.client("datazone", region_name=region),
            config=config,
        )
        project_ids = [
            project.project_id for _, project in config.ordered_projects() if project.project_id
        ]
        project_id = service.find_project_for_principal(principal, project_ids=project_ids)
    except DataZoneError:
        return _response(503, {"error": "authorization service unavailable"})

    if not project_id:
        return _response(403, {"decision": "DENY", "error": "principal project not found"})

    try:
        allowed = service.has_package_grant(project_id=project_id, quilt_uri=quilt_uri)
    except DataZoneError:
        return _response(503, {"error": "authorization service unavailable"})
    except ClientError, BotoCoreError:
        return _response(503, {"error": "authorization service unavailable"})

    if not allowed:
        return _response(403, {"decision": "DENY"})

    secrets = boto3.client("secretsmanager", region_name=region)
    try:
        secret_kwargs: dict[str, str] = {"SecretId": jwt_secret_arn}
        if jwt_secret_version:
            secret_kwargs["VersionId"] = jwt_secret_version
        jwt_secret = secrets.get_secret_value(**secret_kwargs)["SecretString"]
    except ClientError, BotoCoreError, KeyError:
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
