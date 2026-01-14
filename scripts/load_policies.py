#!/usr/bin/env python3
"""Load Cedar policy files to AWS Verified Permissions."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError


def _load_policy_files(policies_dir: Path) -> list[str]:
    """Load all .cedar policy files from directory."""
    policy_files = sorted(policies_dir.glob("*.cedar"))

    if not policy_files:
        print(f"⚠ No .cedar files found in {policies_dir}")
        sys.exit(1)

    return [path.read_text(encoding="utf-8") for path in policy_files]


def _create_policy(
    client: Any, policy_store_id: str, statement: str, dry_run: bool = False
) -> None:
    """Create a single policy in AVP."""
    if dry_run:
        print(f"  [DRY-RUN] Would create policy: {statement[:80]}...")
        return

    try:
        client.create_policy(
            policyStoreId=policy_store_id,
            definition={"static": {"statement": statement}},
        )
        print("✓ Created policy")
    except ClientError as e:
        error_code = e.response["Error"]["Code"]
        if error_code == "ValidationException":
            print(f"✗ Invalid policy syntax: {e}")
        elif error_code == "ConflictException":
            print("⚠ Policy already exists (skipping)")
            return
        else:
            print(f"✗ Failed to create policy: {e}")
        raise


def main() -> None:
    """Load Cedar policies to AWS Verified Permissions."""
    # Parse arguments
    dry_run = "--dry-run" in sys.argv

    # Get configuration
    policy_store_id = os.environ.get("POLICY_STORE_ID")
    if not policy_store_id:
        print("✗ POLICY_STORE_ID environment variable is required", file=sys.stderr)
        sys.exit(1)

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        print("✗ AWS_REGION environment variable is required", file=sys.stderr)
        sys.exit(1)

    # Load policies
    repo_root = Path(__file__).resolve().parents[1]
    policies_dir = repo_root / "policies"

    if not policies_dir.exists():
        print(f"✗ Policies directory not found: {policies_dir}", file=sys.stderr)
        sys.exit(1)

    policies = _load_policy_files(policies_dir)

    print(f"{'='*60}")
    print(f"Loading {len(policies)} policies to AVP")
    print(f"Policy Store: {policy_store_id}")
    print(f"Region: {region}")
    if dry_run:
        print("Mode: DRY-RUN (no changes will be made)")
    print(f"{'='*60}\n")

    # Create client
    try:
        client = boto3.client("verifiedpermissions", region_name=region)
    except Exception as e:
        print(f"✗ Failed to create AWS client: {e}", file=sys.stderr)
        sys.exit(1)

    # Load each policy
    success_count = 0
    skip_count = 0
    fail_count = 0

    for i, statement in enumerate(policies, 1):
        print(f"[{i}/{len(policies)}] Loading policy...")
        try:
            _create_policy(client, policy_store_id, statement, dry_run)
            success_count += 1
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException":
                skip_count += 1
            else:
                fail_count += 1
                continue
        except Exception as e:
            print(f"  Unexpected error: {e}")
            fail_count += 1
            continue

    print(f"\n{'='*60}")
    if dry_run:
        print(f"✓ DRY-RUN: Would load {len(policies)} policies")
    else:
        print(f"✓ Loaded {success_count}/{len(policies)} policies successfully")
        if skip_count > 0:
            print(f"⚠ Skipped {skip_count} existing policies")
        if fail_count > 0:
            print(f"✗ Failed to load {fail_count} policies")
    print(f"{'='*60}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
