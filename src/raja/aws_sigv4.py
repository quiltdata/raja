from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

import httpx

_LAMBDA_URL_REGION_RE = re.compile(r"\.lambda-url\.([a-z0-9-]+)\.on\.aws$")


def _infer_region(url: str) -> str:
    from botocore.session import get_session

    host = urlsplit(url).hostname or ""
    if match := _LAMBDA_URL_REGION_RE.search(host):
        return match.group(1)

    session = get_session()
    region = session.get_config_variable("region")
    if isinstance(region, str) and region.strip():
        return region
    raise RuntimeError(f"Unable to determine AWS region for SigV4 request: {url}")


def build_sigv4_headers(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    service: str = "lambda",
    region: str | None = None,
) -> dict[str, str]:
    from botocore.auth import SigV4Auth
    from botocore.awsrequest import AWSRequest
    from botocore.session import get_session

    session = get_session()
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError("AWS credentials not available for SigV4 signing")

    frozen = credentials.get_frozen_credentials()
    aws_request = AWSRequest(
        method=method.upper(),
        url=url,
        data=body,
        headers=dict(headers or {}),
    )
    SigV4Auth(frozen, service, region or _infer_region(url)).add_auth(aws_request)
    return dict(aws_request.headers.items())


def build_sigv4_request(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str] | None = None,
    body: bytes | None = None,
    service: str = "lambda",
    region: str | None = None,
) -> httpx.Request:
    signed_headers = build_sigv4_headers(
        method=method,
        url=url,
        headers=headers,
        body=body,
        service=service,
        region=region,
    )
    return httpx.Request(method=method.upper(), url=url, headers=signed_headers, content=body)
