from __future__ import annotations

from collections.abc import Iterable, Mapping

import structlog

logger = structlog.get_logger(__name__)


def extract_bearer_token(auth_header: str) -> str:
    """Extract the bearer token from an Authorization header."""
    if not auth_header:
        raise ValueError("Authorization header missing")
    if not auth_header.startswith("Bearer "):
        raise ValueError("Invalid Authorization header")
    token = auth_header[7:].strip()
    if not token:
        raise ValueError("Bearer token missing")
    return token


def construct_request_string(method: str, path: str, query: Mapping[str, str] | None = None) -> str:
    """Construct an S3 request string for prefix-based authorization."""
    method = method.upper()
    parts = [part for part in path.split("/") if part]

    if not parts:
        raise ValueError("Invalid path: empty")

    bucket = parts[0]
    key = "/".join(parts[1:]) if len(parts) > 1 else ""
    query_params = query or {}

    if method == "GET" and ("list-type" in query_params or not key):
        action = "s3:ListBucket"
        resource = f"{bucket}/"
    elif method == "GET":
        action = "s3:GetObject"
        resource = f"{bucket}/{key}"
    elif method == "PUT":
        if not key:
            raise ValueError("PUT requires an object key")
        action = "s3:PutObject"
        resource = f"{bucket}/{key}"
    elif method == "DELETE":
        if not key:
            raise ValueError("DELETE requires an object key")
        action = "s3:DeleteObject"
        resource = f"{bucket}/{key}"
    elif method == "HEAD":
        if not key:
            raise ValueError("HEAD requires an object key")
        action = "s3:HeadObject"
        resource = f"{bucket}/{key}"
    else:
        raise ValueError(f"Unsupported HTTP method: {method}")

    return f"{action}/{resource}"


def is_authorized(request_string: str, grants: Iterable[str]) -> bool:
    """Check if request is covered by any grant using prefix matching."""
    grant_list = list(grants)
    for grant in grant_list:
        if request_string.startswith(grant):
            logger.debug("authorization_granted", request=request_string, grant=grant)
            return True

    logger.warning("authorization_denied", request=request_string, grants=grant_list)
    return False
