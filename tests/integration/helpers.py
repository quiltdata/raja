import base64
import json
import logging
import os
from pathlib import Path
from typing import Any
from urllib import error, parse, request
from urllib.parse import urlsplit

import pytest

OUTPUT_FILES = (
    Path("infra") / "cdk-outputs.json",
    Path("cdk-outputs.json"),
    Path("infra") / "cdk.out" / "outputs.json",
)

logger = logging.getLogger(__name__)


def _extract_output_value(payload: Any, key: str) -> str | None:
    if isinstance(payload, dict):
        if isinstance(payload.get(key), str):
            return payload[key]
        for value in payload.values():
            if isinstance(value, dict):
                nested = _extract_output_value(value, key)
                if nested:
                    return nested
    return None


def _load_api_url_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        api_url = _extract_output_value(payload, "ApiUrl")
        if api_url:
            return api_url
    return None


def _load_rajee_bucket_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        bucket = _extract_output_value(payload, "TestBucketName")
        if bucket:
            return bucket
    return None


def _load_rajee_endpoint_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        endpoint = _extract_output_value(payload, "RajeeEndpoint")
        if endpoint:
            return endpoint
    return None


def _load_rale_authorizer_url_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        endpoint = _extract_output_value(payload, "RaleAuthorizerUrl")
        if endpoint:
            return endpoint
    return None


def _load_rale_router_url_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        endpoint = _extract_output_value(payload, "RaleRouterUrl")
        if endpoint:
            return endpoint
    return None


def _load_jwt_secret_arn_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        secret_arn = _extract_output_value(payload, "JWTSecretArn") or _extract_output_value(
            payload, "JwtSecretArn"
        )
        if secret_arn:
            return secret_arn
    return None


def _load_taj_cache_table_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        name = _extract_output_value(payload, "TajCacheTable")
        if name:
            return name
    return None


def _load_manifest_cache_table_from_outputs(repo_root: Path) -> str | None:
    for relative in OUTPUT_FILES:
        path = repo_root / relative
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        name = _extract_output_value(payload, "ManifestCacheTable")
        if name:
            return name
    return None


def require_api_url() -> str:
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        repo_root = Path(__file__).resolve().parents[2]
        api_url = _load_api_url_from_outputs(repo_root)
    if not api_url:
        pytest.skip("RAJA_API_URL not set")
    return api_url.rstrip("/")


def require_jwt_secret_arn() -> str:
    secret_arn = os.environ.get("JWT_SECRET_ARN")
    if not secret_arn:
        repo_root = Path(__file__).resolve().parents[2]
        secret_arn = _load_jwt_secret_arn_from_outputs(repo_root)
    if not secret_arn:
        pytest.skip("JWT_SECRET_ARN not set")
    return secret_arn


def require_api_issuer() -> str:
    api_url = require_api_url()
    parts = urlsplit(api_url)
    return f"{parts.scheme}://{parts.netloc}"


def require_rajee_test_bucket() -> str:
    bucket = os.environ.get("RAJEE_TEST_BUCKET")
    if not bucket:
        repo_root = Path(__file__).resolve().parents[2]
        bucket = _load_rajee_bucket_from_outputs(repo_root)
    if not bucket:
        pytest.skip("RAJEE_TEST_BUCKET not set")
    return bucket


def require_rajee_endpoint() -> str:
    endpoint = os.environ.get("RAJEE_ENDPOINT")
    if not endpoint:
        repo_root = Path(__file__).resolve().parents[2]
        endpoint = _load_rajee_endpoint_from_outputs(repo_root)
    if not endpoint:
        pytest.skip("RAJEE_ENDPOINT not set")
    return endpoint.rstrip("/")


def require_rale_authorizer_url() -> str:
    endpoint = os.environ.get("RALE_AUTHORIZER_URL")
    if not endpoint:
        repo_root = Path(__file__).resolve().parents[2]
        endpoint = _load_rale_authorizer_url_from_outputs(repo_root)
    if not endpoint:
        pytest.skip("RALE_AUTHORIZER_URL not set")
    return endpoint.rstrip("/")


def require_rale_router_url() -> str:
    endpoint = os.environ.get("RALE_ROUTER_URL")
    if not endpoint:
        repo_root = Path(__file__).resolve().parents[2]
        endpoint = _load_rale_router_url_from_outputs(repo_root)
    if not endpoint:
        pytest.skip("RALE_ROUTER_URL not set")
    return endpoint.rstrip("/")


def require_taj_cache_table() -> str:
    table_name = os.environ.get("TAJ_CACHE_TABLE")
    if not table_name:
        repo_root = Path(__file__).resolve().parents[2]
        table_name = _load_taj_cache_table_from_outputs(repo_root)
    if not table_name:
        pytest.skip("TAJ_CACHE_TABLE not set")
    return table_name


def require_manifest_cache_table() -> str:
    table_name = os.environ.get("MANIFEST_CACHE_TABLE")
    if not table_name:
        repo_root = Path(__file__).resolve().parents[2]
        table_name = _load_manifest_cache_table_from_outputs(repo_root)
    if not table_name:
        pytest.skip("MANIFEST_CACHE_TABLE not set")
    return table_name


def is_rale_routing_enabled() -> bool:
    authorizer = os.environ.get("RALE_AUTHORIZER_URL")
    router = os.environ.get("RALE_ROUTER_URL")
    if authorizer and router:
        return True

    repo_root = Path(__file__).resolve().parents[2]
    authorizer = _load_rale_authorizer_url_from_outputs(repo_root)
    router = _load_rale_router_url_from_outputs(repo_root)
    return bool(authorizer and router)


def request_json(
    method: str, path: str, body: dict[str, Any] | None = None, query: dict[str, str] | None = None
) -> tuple[int, dict[str, Any]]:
    base_url = require_api_url()
    url = f"{base_url}/{path.lstrip('/')}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"

    data = None
    headers = {"Content-Type": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")

    req = request.Request(url, data=data, headers=headers, method=method)
    try:
        with request.urlopen(req) as response:
            payload = response.read()
            status = response.status
    except error.HTTPError as exc:
        payload = exc.read()
        status = exc.code

    if not payload:
        return status, {}

    return status, json.loads(payload.decode("utf-8"))


def request_url(
    method: str,
    url: str,
    headers: dict[str, str] | None = None,
    body: bytes | None = None,
) -> tuple[int, dict[str, str], bytes]:
    req = request.Request(url, data=body, headers=headers or {}, method=method)
    try:
        with request.urlopen(req) as response:
            payload = response.read()
            status = response.status
            response_headers = dict(response.headers.items())
    except error.HTTPError as exc:
        payload = exc.read()
        status = exc.code
        response_headers = dict(exc.headers.items())
    return status, response_headers, payload


def issue_token(principal: str) -> tuple[str, list[str]]:
    status, body = request_json("POST", "/token", {"principal": principal})
    assert status == 200, body
    token = body.get("token")
    scopes = body.get("scopes", [])
    assert token, "token missing in response"
    return token, scopes


def issue_rajee_token(principal: str = "test-user") -> tuple[str, list[str]]:
    """Issue a RAJEE token via the control plane (signed by JWKS secret)."""
    status, body = request_json(
        "POST",
        "/token",
        {"principal": principal, "token_type": "rajee"},
    )
    assert status == 200, body
    token = body.get("token")
    scopes = body.get("scopes", [])
    assert token, "token missing in response"
    return token, scopes


def fetch_jwks_secret() -> str:
    status, body = request_json("GET", "/.well-known/jwks.json")
    assert status == 200, body
    keys = body.get("keys", [])
    assert keys, "JWKS keys missing"
    jwks_key = keys[0].get("k")
    assert jwks_key, "JWKS key material missing"
    padding = "=" * (-len(jwks_key) % 4)
    return base64.urlsafe_b64decode(jwks_key + padding).decode("utf-8")
