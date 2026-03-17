#!/usr/bin/env python3
"""Print a human-readable summary of RAJA deployment outputs."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import quote

_OUTPUTS_PATH = Path(__file__).parent.parent / "infra" / "tf-outputs.json"
_ENV_PATH = Path(__file__).parent.parent / ".env"


def _load_dotenv() -> None:
    """Parse .env and populate os.environ for any keys not already set."""
    if not _ENV_PATH.exists():
        return
    for line in _ENV_PATH.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("\"'")
        if key and key not in os.environ:
            os.environ[key] = value


# (label, key, fallback_when_empty)
_SECTIONS: list[tuple[str, list[tuple[str, str, str]]]] = [
    ("Control Plane", [
        ("Admin UI / API URL", "api_url", ""),
    ]),
    ("DataZone", [
        ("Domain ID", "datazone_domain_id", ""),
        ("Portal URL", "datazone_portal_url", ""),
        ("Owner Project ID", "datazone_owner_project_id", ""),
        ("Asset Type", "datazone_package_asset_type", ""),
    ]),
    ("RALE", [
        ("Authorizer URL", "rale_authorizer_url", ""),
        ("Router URL", "rale_router_url", ""),
        ("RAJEE Proxy Endpoint", "rajee_endpoint", ""),
        ("Test Bucket", "rajee_test_bucket_name", ""),
        ("Registry Bucket", "rajee_registry_bucket_name", ""),
        (
            "Envoy Debug UI (9901)",
            "rajee_admin_url",
            "(disabled — set admin_allowed_cidrs to enable)",
        ),
    ]),
    ("Envoy", [
        ("Repository URI", "envoy_repository_uri", ""),
        ("Image Tag", "envoy_image_tag", ""),
    ]),
]


def _region_from_arn(arn: str) -> str:
    """Extract region from an ARN string."""
    parts = arn.split(":")
    return parts[3] if len(parts) > 3 else "us-east-1"


def _fn_name_from_arn(arn: str) -> str:
    """Extract Lambda function name from an ARN."""
    # arn:aws:lambda:region:account:function:name
    parts = arn.split(":")
    return parts[6] if len(parts) > 6 else arn


def _logs_url(region: str, log_group: str) -> str:
    encoded = quote(log_group, safe="").replace("%", "$25")
    return f"https://console.aws.amazon.com/cloudwatch/home?region={region}#logsV2:log-groups/log-group/{encoded}"


def _lambda_console_url(region: str, fn_name: str) -> str:
    return f"https://console.aws.amazon.com/lambda/home?region={region}#/functions/{fn_name}"


def _apigw_console_url(api_url: str) -> str:
    m = re.match(r"https://([^.]+)\.execute-api\.([^.]+)\.amazonaws\.com", api_url)
    if not m:
        return ""
    api_id, region = m.group(1), m.group(2)
    return f"https://console.aws.amazon.com/apigateway/home?region={region}#/apis/{api_id}/resources"


def _secret_console_url(secret_arn: str) -> str:
    region = _region_from_arn(secret_arn)
    # arn:aws:secretsmanager:region:account:secret:name-suffix
    parts = secret_arn.split(":")
    secret_name = parts[6] if len(parts) > 6 else secret_arn
    return f"https://console.aws.amazon.com/secretsmanager/secret?name={secret_name}&region={region}"


def _ecr_console_url(repo_uri: str) -> str:
    # 712023778557.dkr.ecr.us-east-1.amazonaws.com/raja/envoy
    m = re.match(r"(\d+)\.dkr\.ecr\.([^.]+)\.amazonaws\.com/(.+)", repo_uri)
    if not m:
        return ""
    account, region, repo_name = m.group(1), m.group(2), m.group(3)
    return f"https://console.aws.amazon.com/ecr/repositories/private/{account}/{repo_name}?region={region}"


def _s3_console_url(bucket: str) -> str:
    return f"https://s3.console.aws.amazon.com/s3/buckets/{bucket}"


def _ecs_console_url(region: str, cluster: str, service: str) -> str:
    return (
        f"https://console.aws.amazon.com/ecs/v2/clusters/{cluster}"
        f"/services/{service}/health?region={region}"
    )


def _build_console_section(outputs: dict[str, str]) -> list[tuple[str, str]]:
    """Derive AWS Console links from deployment outputs."""
    rows: list[tuple[str, str]] = []

    # API Gateway
    api_url = outputs.get("api_url", "")
    if url := _apigw_console_url(api_url):
        rows.append(("API Gateway", url))

    # Control plane Lambda
    cp_arn = outputs.get("control_plane_lambda_arn", "")
    if cp_arn:
        region = _region_from_arn(cp_arn)
        fn = _fn_name_from_arn(cp_arn)
        rows.append(("Control Plane Lambda", _lambda_console_url(region, fn)))
        rows.append(("Control Plane Logs", _logs_url(region, f"/aws/lambda/{fn}")))

    # RALE authorizer Lambda
    auth_arn = outputs.get("rale_authorizer_arn", "")
    if auth_arn:
        region = _region_from_arn(auth_arn)
        fn = _fn_name_from_arn(auth_arn)
        rows.append(("RALE Authorizer Lambda", _lambda_console_url(region, fn)))
        rows.append(("RALE Authorizer Logs", _logs_url(region, f"/aws/lambda/{fn}")))

    # RALE router Lambda
    router_arn = outputs.get("rale_router_arn", "")
    if router_arn:
        region = _region_from_arn(router_arn)
        fn = _fn_name_from_arn(router_arn)
        rows.append(("RALE Router Lambda", _lambda_console_url(region, fn)))
        rows.append(("RALE Router Logs", _logs_url(region, f"/aws/lambda/{fn}")))

    # Secrets Manager
    jwt_arn = outputs.get("jwt_secret_arn", "")
    if jwt_arn:
        rows.append(("JWT Secret", _secret_console_url(jwt_arn)))

    # ECR
    repo_uri = outputs.get("envoy_repository_uri", "")
    if repo_uri:
        rows.append(("Envoy ECR Repo", _ecr_console_url(repo_uri)))

    # S3 buckets
    for label, key in [
        ("Test Bucket (S3)", "rajee_test_bucket_name"),
        ("Registry Bucket (S3)", "rajee_registry_bucket_name"),
    ]:
        if bucket := outputs.get(key, ""):
            rows.append((label, _s3_console_url(bucket)))

    # ECS service
    cluster = outputs.get("ecs_cluster_name", "")
    service = outputs.get("ecs_service_name", "")
    if cluster and service:
        # derive region from a known ARN
        region = _region_from_arn(outputs.get("control_plane_lambda_arn", "::::::::"))
        rows.append(("RAJEE ECS Service", _ecs_console_url(region, cluster, service)))

    return rows


def main() -> None:
    _load_dotenv()

    if not _OUTPUTS_PATH.exists():
        print("ERROR: infra/tf-outputs.json not found — run ./poe deploy first.", file=sys.stderr)
        sys.exit(1)

    outputs: dict[str, str] = json.loads(_OUTPUTS_PATH.read_text())

    admin_key = os.environ.get("RAJA_ADMIN_KEY", "")

    width = 72
    print()
    print("=" * width)
    print(" RAJA Deployment Outputs".center(width))
    print("=" * width)

    for section_title, fields in _SECTIONS:
        print(f"\n  {section_title}")
        print("  " + "-" * (width - 2))
        for label, key, fallback in fields:
            value = outputs.get(key, "") or fallback
            if value:
                print(f"  {label:<24} {value}")

    print("\n  Admin Auth")
    print("  " + "-" * (width - 2))
    if admin_key:
        print(f"  {'Admin Key':<24} {admin_key}")
    else:
        print(f"  {'Admin Key':<24} (set RAJA_ADMIN_KEY to display)")

    console_rows = _build_console_section(outputs)
    if console_rows:
        print("\n  AWS Console")
        print("  " + "-" * (width - 2))
        for label, url in console_rows:
            print(f"  {label:<24} {url}")

    print()
    print("=" * width)
    print()


if __name__ == "__main__":
    main()
