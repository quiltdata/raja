import json
import os
from pathlib import Path
from typing import Any
from urllib import error, parse, request

import pytest

OUTPUT_FILES = (
    Path("infra") / "cdk-outputs.json",
    Path("cdk-outputs.json"),
    Path("infra") / "cdk.out" / "outputs.json",
)


def _extract_api_url(payload: Any) -> str | None:
    if isinstance(payload, dict):
        if isinstance(payload.get("ApiUrl"), str):
            return payload["ApiUrl"]
        for value in payload.values():
            if isinstance(value, dict) and isinstance(value.get("ApiUrl"), str):
                return value["ApiUrl"]
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
        api_url = _extract_api_url(payload)
        if api_url:
            return api_url
    return None


def require_api_url() -> str:
    api_url = os.environ.get("RAJA_API_URL")
    if not api_url:
        repo_root = Path(__file__).resolve().parents[2]
        api_url = _load_api_url_from_outputs(repo_root)
    if not api_url:
        pytest.skip("RAJA_API_URL not set")
    return api_url.rstrip("/")


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
