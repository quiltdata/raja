#!/usr/bin/env python3
"""Seed test principals into DynamoDB for integration testing."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import boto3
from botocore.exceptions import ClientError

from raja.datazone import DataZoneConfig, DataZoneError, DataZoneService, datazone_enabled


def _get_region() -> str:
    """Get AWS region from environment."""
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        print("✗ AWS_REGION environment variable is required", file=sys.stderr)
        sys.exit(1)
    return region


def _load_test_data(data_file: Path | None = None) -> dict[str, list[str]]:
    """Load test data from file or use defaults."""
    if data_file:
        if not data_file.exists():
            print(f"✗ Data file not found: {data_file}", file=sys.stderr)
            sys.exit(1)
        try:
            with open(data_file) as f:
                data = json.load(f)
            print(f"✓ Loaded test data from {data_file}")
            return data
        except json.JSONDecodeError as e:
            print(f"✗ Invalid JSON in {data_file}: {e}", file=sys.stderr)
            sys.exit(1)

    # Default test data
    return {
        "alice": ["Document:doc123:read", "Document:doc123:write"],
        "bob": ["Document:doc123:read"],
        "admin": ["Document:*:*"],
        "guest": ["Document:public:read"],
        "test-user": ["Document:public:read"],
    }


def _seed_principal(
    table: Any,
    principal: str,
    scopes: list[str],
    dry_run: bool = False,
    datazone_service: DataZoneService | None = None,
) -> None:
    """Seed a single principal into DynamoDB."""
    item: dict[str, Any] = {"principal": principal, "scopes": scopes}
    if datazone_service is not None:
        project = datazone_service.ensure_project_for_principal(principal)
        item["datazone_project_id"] = project["project_id"]
        item["datazone_project_name"] = project["project_name"]
    if dry_run:
        project_id = item.get("datazone_project_id")
        suffix = f", project={project_id}" if project_id else ""
        print(f"  [DRY-RUN] Would seed: {principal} with {len(scopes)} scopes{suffix}")
        return

    try:
        table.put_item(Item=item)
        print(f"✓ Seeded principal: {principal} ({len(scopes)} scopes)")
    except ClientError as e:
        print(f"✗ Failed to seed {principal}: {e}", file=sys.stderr)
        raise


def main() -> None:
    """Seed test principals into DynamoDB."""
    # Parse arguments
    dry_run = "--dry-run" in sys.argv
    data_file = None

    if "--data" in sys.argv:
        idx = sys.argv.index("--data")
        if idx + 1 < len(sys.argv):
            data_file = Path(sys.argv[idx + 1])
        else:
            print("✗ --data requires a file path argument", file=sys.stderr)
            sys.exit(1)

    # Get configuration
    table_name = os.environ.get("PRINCIPAL_TABLE")
    if not table_name:
        print("✗ PRINCIPAL_TABLE environment variable is required", file=sys.stderr)
        sys.exit(1)

    region = _get_region()

    # Load test data
    principals = _load_test_data(data_file)

    if not principals:
        print("⚠ No principals to seed", file=sys.stderr)
        sys.exit(0)

    print(f"{'=' * 60}")
    print(f"Seeding {len(principals)} test principals")
    print(f"Table: {table_name}")
    print(f"Region: {region}")
    if dry_run:
        print("Mode: DRY-RUN (no changes will be made)")
    if data_file:
        print(f"Data source: {data_file}")
    else:
        print("Data source: Built-in defaults")
    print(f"{'=' * 60}\n")

    # Create DynamoDB resource
    try:
        dynamodb = boto3.resource("dynamodb", region_name=region)
        table = dynamodb.Table(table_name)
    except Exception as e:
        print(f"✗ Failed to create DynamoDB resource: {e}", file=sys.stderr)
        sys.exit(1)

    datazone_service: DataZoneService | None = None
    if datazone_enabled():
        try:
            datazone_service = DataZoneService(
                client=boto3.client("datazone", region_name=region),
                config=DataZoneConfig.from_env(),
            )
            print(f"DataZone domain: {datazone_service.domain_id}")
        except DataZoneError as e:
            print(f"✗ Failed to initialize DataZone: {e}", file=sys.stderr)
            sys.exit(1)

    # Seed each principal
    success_count = 0
    fail_count = 0

    for principal, scopes in principals.items():
        print(f"[{success_count + fail_count + 1}/{len(principals)}] Seeding {principal}...")
        try:
            _seed_principal(
                table,
                principal,
                scopes,
                dry_run,
                datazone_service=datazone_service,
            )
            success_count += 1
        except Exception as e:
            print(f"  Unexpected error: {e}")
            fail_count += 1
            continue

    print(f"\n{'=' * 60}")
    if dry_run:
        print(f"✓ DRY-RUN: Would seed {len(principals)} principals")
    else:
        print(f"✓ Seeded {success_count}/{len(principals)} principals successfully")
        if fail_count > 0:
            print(f"✗ Failed to seed {fail_count} principals")
    print(f"{'=' * 60}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
