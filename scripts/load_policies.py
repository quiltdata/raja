from __future__ import annotations

import os
from pathlib import Path

import boto3


def _load_policy_files(policies_dir: Path) -> list[str]:
    return [path.read_text(encoding="utf-8") for path in sorted(policies_dir.glob("*.cedar"))]


def main() -> None:
    policy_store_id = os.environ.get("POLICY_STORE_ID")
    if not policy_store_id:
        raise SystemExit("POLICY_STORE_ID is required")
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        raise SystemExit("AWS_REGION is required")

    repo_root = Path(__file__).resolve().parents[1]
    policies_dir = repo_root / "policies" / "policies"
    policies = _load_policy_files(policies_dir)

    client = boto3.client("verifiedpermissions", region_name=region)
    for statement in policies:
        client.create_policy(
            policyStoreId=policy_store_id,
            definition={"static": {"statement": statement}},
        )


if __name__ == "__main__":
    main()
