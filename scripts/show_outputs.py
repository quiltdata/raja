#!/usr/bin/env python3
"""Print a human-readable summary of RAJA deployment outputs."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

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
        ("Policy Store ID", "policy_store_id", ""),
    ]),
    ("RALE", [
        ("Authorizer URL", "rale_authorizer_url", ""),
        ("Router URL", "rale_router_url", ""),
        ("RAJEE Proxy Endpoint", "rajee_endpoint", ""),
        ("Test Bucket", "rajee_test_bucket_name", ""),
        ("Registry Bucket", "rajee_registry_bucket_name", ""),
        ("Envoy Debug UI (9901)", "rajee_admin_url", "(disabled — set admin_allowed_cidrs to enable)"),
    ]),
    ("Envoy", [
        ("Repository URI", "envoy_repository_uri", ""),
        ("Image Tag", "envoy_image_tag", ""),
    ]),
]


def main() -> None:
    _load_dotenv()

    if not _OUTPUTS_PATH.exists():
        print("ERROR: infra/tf-outputs.json not found — run ./poe deploy first.", file=sys.stderr)
        sys.exit(1)

    outputs: dict[str, str] = json.loads(_OUTPUTS_PATH.read_text())

    admin_key = os.environ.get("RAJA_ADMIN_KEY") or os.environ.get("TF_VAR_raja_admin_key", "")

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

    print(f"\n  Admin Auth")
    print("  " + "-" * (width - 2))
    if admin_key:
        print(f"  {'Admin Key':<24} {admin_key}")
    else:
        print(f"  {'Admin Key':<24} (set RAJA_ADMIN_KEY to display)")

    print()
    print("=" * width)
    print()


if __name__ == "__main__":
    main()
