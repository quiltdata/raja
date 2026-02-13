#!/usr/bin/env python3
"""Apply Cedar schema to AVP policy store and enable STRICT validation."""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import boto3


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--policy-store-id", required=True)
    parser.add_argument("--schema-path", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--namespace", default="Raja")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(repo_root / "src"))

    from raja.cedar.schema import parse_cedar_schema_to_avp_json

    schema_text = Path(args.schema_path).read_text(encoding="utf-8")
    cedar_json = parse_cedar_schema_to_avp_json(schema_text, namespace=args.namespace)

    client = boto3.client("verifiedpermissions", region_name=args.region)
    max_attempts = 12

    for attempt in range(1, max_attempts + 1):
        try:
            client.put_schema(
                policyStoreId=args.policy_store_id,
                definition={"cedarJson": cedar_json},
            )
            client.update_policy_store(
                policyStoreId=args.policy_store_id,
                validationSettings={"mode": "STRICT"},
            )
            return 0
        except Exception:
            if attempt == max_attempts:
                raise
            time.sleep(2.0)

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
