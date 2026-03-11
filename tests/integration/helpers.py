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
    Path("infra") / "tf-outputs.json",
    Path("tf-outputs.json"),
)

logger = logging.getLogger(__name__)
_PUBLIC_CONTROL_PLANE_PATHS = {"/", "/health", "/.well-known/jwks.json"}
_REPO_ROOT = Path(__file__).resolve().parents[2]
_RALE_URI_FILE = _REPO_ROOT / ".rale-test-uri"


def _load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for raw_line in path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key:
            os.environ.setdefault(key, value)


_load_dotenv(_REPO_ROOT / ".env")


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
        api_url = _extract_output_value(payload, "api_url")
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
        bucket = _extract_output_value(payload, "rajee_test_bucket_name")
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
        endpoint = _extract_output_value(payload, "rajee_endpoint")
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
        endpoint = _extract_output_value(payload, "rale_authorizer_url")
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
        endpoint = _extract_output_value(payload, "rale_router_url")
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
        secret_arn = _extract_output_value(payload, "jwt_secret_arn")
        if secret_arn:
            return secret_arn
    return None


def require_api_url() -> str:
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        api_url = _load_api_url_from_outputs(_REPO_ROOT)
    if not api_url:
        pytest.skip("RAJA_API_URL not set")
    return api_url.rstrip("/")


def require_jwt_secret_arn() -> str:
    secret_arn = os.environ.get("JWT_SECRET_ARN")
    if not secret_arn:
        secret_arn = _load_jwt_secret_arn_from_outputs(_REPO_ROOT)
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
        bucket = _load_rajee_bucket_from_outputs(_REPO_ROOT)
    if not bucket:
        pytest.skip("RAJEE_TEST_BUCKET not set")
    return bucket


def require_rajee_endpoint() -> str:
    endpoint = os.environ.get("RAJEE_ENDPOINT")
    if not endpoint:
        endpoint = _load_rajee_endpoint_from_outputs(_REPO_ROOT)
    if not endpoint:
        pytest.skip("RAJEE_ENDPOINT not set")
    return endpoint.rstrip("/")


def require_rale_authorizer_url() -> str:
    endpoint = os.environ.get("RALE_AUTHORIZER_URL")
    if not endpoint:
        endpoint = _load_rale_authorizer_url_from_outputs(_REPO_ROOT)
    if not endpoint:
        pytest.skip("RALE_AUTHORIZER_URL not set")
    return endpoint.rstrip("/")


def require_rale_router_url() -> str:
    endpoint = os.environ.get("RALE_ROUTER_URL")
    if not endpoint:
        endpoint = _load_rale_router_url_from_outputs(_REPO_ROOT)
    if not endpoint:
        pytest.skip("RALE_ROUTER_URL not set")
    return endpoint.rstrip("/")


def require_rale_test_quilt_uri() -> str:
    """Return the test package URI or fail loudly if test data is missing."""
    uri = os.environ.get("RALE_TEST_QUILT_URI") or os.environ.get("TEST_PACKAGE")
    if not uri and _RALE_URI_FILE.is_file():
        uri = _RALE_URI_FILE.read_text().strip()
    if not uri:
        pytest.fail(
            "RALE_TEST_QUILT_URI (or TEST_PACKAGE) is not set and .rale-test-uri does not exist.\n"
            "Run: python scripts/seed_packages.py\n"
            "Then set RALE_TEST_QUILT_URI=<printed URI> (or TEST_PACKAGE=<URI>) "
            "or rely on .rale-test-uri"
        )
    return uri


def parse_rale_test_quilt_uri(uri: str) -> dict[str, str]:
    """Parse quilt+s3://bucket#package=author/name@hash for RALE integration tests."""
    try:
        scheme, rest = uri.split("://", 1)
        storage = scheme.removeprefix("quilt+")
        registry, fragment = rest.split("#", 1)
        package_ref = fragment.removeprefix("package=")
        package_name, top_hash = package_ref.rsplit("@", 1)
    except ValueError as exc:
        raise ValueError(
            "invalid RALE test URI; expected quilt+s3://<bucket>#package=<author/name>@<hash>"
        ) from exc
    if not storage or not registry or not package_name or not top_hash:
        raise ValueError(
            "invalid RALE test URI; expected quilt+s3://<bucket>#package=<author/name>@<hash>"
        )
    return {
        "storage": storage,
        "registry": registry,
        "package_name": package_name,
        "hash": top_hash,
    }


def require_taj_cache_table() -> str:
    pytest.fail(
        "TAJ cache table support was removed from RALE.\n"
        "Use real package-backed integration tests with RALE_TEST_QUILT_URI instead."
    )


def require_manifest_cache_table() -> str:
    pytest.fail(
        "Manifest cache table support was removed from RALE.\n"
        "Use real package-backed integration tests with RALE_TEST_QUILT_URI instead."
    )


def is_rale_routing_enabled() -> bool:
    authorizer = os.environ.get("RALE_AUTHORIZER_URL")
    router = os.environ.get("RALE_ROUTER_URL")
    if authorizer and router:
        return True

    authorizer = _load_rale_authorizer_url_from_outputs(_REPO_ROOT)
    router = _load_rale_router_url_from_outputs(_REPO_ROOT)
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
    normalized_path = f"/{path.lstrip('/')}"
    if normalized_path not in _PUBLIC_CONTROL_PLANE_PATHS:
        admin_key = os.environ.get("RAJA_ADMIN_KEY")
        if not admin_key:
            pytest.skip("RAJA_ADMIN_KEY not set for protected control-plane endpoint tests")
        headers["Authorization"] = f"Bearer {admin_key}"
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
