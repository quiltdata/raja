import json
import logging
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import jwt
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


def require_api_url() -> str:
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        repo_root = Path(__file__).resolve().parents[2]
        api_url = _load_api_url_from_outputs(repo_root)
    if not api_url:
        pytest.skip("RAJA_API_URL not set")
    return api_url.rstrip("/")


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


def issue_token(principal: str) -> tuple[str, list[str]]:
    status, body = request_json("POST", "/token", {"principal": principal})
    assert status == 200, body
    token = body.get("token")
    scopes = body.get("scopes", [])
    assert token, "token missing in response"
    return token, scopes


def get_jwt_secret() -> str:
    """Get JWT signing secret for test token generation."""
    secret = os.environ.get("JWT_SECRET")
    if secret:
        return secret

    repo_root = Path(__file__).resolve().parents[2]
    secret_arn = _load_jwt_secret_arn_from_outputs(repo_root)
    if secret_arn:
        try:
            import boto3

            secrets = boto3.client("secretsmanager")
            response = secrets.get_secret_value(SecretId=secret_arn)
            return response["SecretString"]
        except Exception as exc:
            logger.debug("Could not fetch JWT secret from Secrets Manager: %s", exc)

    return "test-secret-key-for-local-testing"


def issue_rajee_token(bucket: str, prefix: str = "rajee-integration/") -> str:
    """Issue a RAJEE token with grants for the test bucket/prefix."""
    issuer = os.environ.get("RAJA_ISSUER")
    if not issuer:
        issuer = require_api_url()

    normalized_prefix = prefix.lstrip("/")
    if normalized_prefix and not normalized_prefix.endswith("/"):
        normalized_prefix = f"{normalized_prefix}/"

    grants = [
        f"s3:GetObject/{bucket}/{normalized_prefix}",
        f"s3:PutObject/{bucket}/{normalized_prefix}",
        f"s3:DeleteObject/{bucket}/{normalized_prefix}",
        f"s3:ListBucket/{bucket}/",
        f"s3:GetObjectAttributes/{bucket}/{normalized_prefix}",
        f"s3:ListObjectVersions/{bucket}/{normalized_prefix}",
    ]

    now = datetime.now(UTC)
    payload = {
        "sub": "User::test-user",
        "iss": issuer,
        "aud": ["raja-s3-proxy"],
        "exp": int((now + timedelta(hours=1)).timestamp()),
        "iat": int(now.timestamp()),
        "grants": grants,
    }

    secret = get_jwt_secret()
    return jwt.encode(payload, secret, algorithm="HS256")
