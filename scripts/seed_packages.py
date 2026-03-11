#!/usr/bin/env python3
"""Seed quilt3 packages into raja-poc-registry using files from raja-poc-test."""

from __future__ import annotations

import os
import sys

import boto3
from botocore.exceptions import ClientError


def _get_region() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        print("✗ AWS_REGION environment variable is required", file=sys.stderr)
        sys.exit(1)
    return region


def _get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def _ensure_test_files(s3: object, bucket: str, dry_run: bool) -> list[tuple[str, str]]:
    """Upload sample files to the test bucket if they don't already exist.

    Returns list of (logical_name, s3_key) tuples.
    """
    files = [
        (
            "data.csv",
            "rajee-integration/package-demo/data.csv",
            "col1,col2,col3\nalpha,1,true\nbeta,2,false\ngamma,3,true\n",
        ),
        (
            "README.md",
            "rajee-integration/package-demo/README.md",
            "# Package Demo\n\nSample dataset for RAJA package-grant integration tests.\n\n"
            "## Files\n\n- `data.csv` — tabular data\n- `results.json` — summary stats\n",
        ),
        (
            "results.json",
            "rajee-integration/package-demo/results.json",
            '{"status": "ok", "row_count": 3, "columns": ["col1", "col2", "col3"]}\n',
        ),
    ]

    entries: list[tuple[str, str]] = []
    for logical_name, key, body in files:
        entries.append((logical_name, key))
        if dry_run:
            print(f"  [DRY-RUN] Would ensure s3://{bucket}/{key}")
            continue
        try:
            s3.head_object(Bucket=bucket, Key=key)  # type: ignore[attr-defined]
            print(f"  ✓ Already exists: s3://{bucket}/{key}")
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "404":
                s3.put_object(  # type: ignore[attr-defined]
                    Bucket=bucket, Key=key, Body=body.encode()
                )
                print(f"  ✓ Created: s3://{bucket}/{key}")
            else:
                raise

    return entries


def _push_package(
    quilt3: object,
    package_name: str,
    entries: list[tuple[str, str]],
    test_bucket: str,
    registry_bucket: str,
    dry_run: bool,
) -> str | None:
    """Build and push a quilt3 package to the registry.

    Physical files remain in test_bucket; the manifest is written to
    registry_bucket under .quilt/packages/.
    """
    pkg = quilt3.Package()  # type: ignore[attr-defined]
    for logical_name, key in entries:
        pkg.set(logical_name, f"s3://{test_bucket}/{key}")

    if dry_run:
        print(
            f"  [DRY-RUN] Would push '{package_name}' to s3://{registry_bucket}"
            f" (data stays in s3://{test_bucket})"
        )
        return None

    # dest points back to test bucket so quilt3 doesn't copy the objects
    dest = f"s3://{test_bucket}/rajee-integration/package-demo"
    top_hash = pkg.push(
        package_name,
        registry=f"s3://{registry_bucket}",
        dest=dest,
    )
    return str(top_hash)


def main() -> None:
    dry_run = "--dry-run" in sys.argv

    test_bucket_override = None
    registry_bucket_override = None
    for flag in ("--test-bucket", "--registry-bucket"):
        if flag in sys.argv:
            idx = sys.argv.index(flag)
            if idx + 1 >= len(sys.argv):
                print(f"✗ {flag} requires a value", file=sys.stderr)
                sys.exit(1)
            value = sys.argv[idx + 1]
            if flag == "--test-bucket":
                test_bucket_override = value
            else:
                registry_bucket_override = value

    region = _get_region()
    account_id = _get_account_id()

    test_bucket = test_bucket_override or f"raja-poc-test-{account_id}-{region}"
    registry_bucket = registry_bucket_override or f"raja-poc-registry-{account_id}-{region}"

    print("=" * 60)
    print("Seeding Quilt packages")
    print(f"Test bucket:     {test_bucket}")
    print(f"Registry bucket: {registry_bucket}")
    print(f"Region:          {region}")
    if dry_run:
        print("Mode: DRY-RUN (no changes will be made)")
    print("=" * 60)
    print()

    try:
        import quilt3  # type: ignore[import-not-found]
    except ImportError:
        print("✗ quilt3 is required: pip install quilt3", file=sys.stderr)
        sys.exit(1)

    s3 = boto3.client("s3", region_name=region)

    # --- Package 1: demo/package-grant ---
    print("[1/1] demo/package-grant")
    print("  Ensuring test files exist in test bucket…")
    entries = _ensure_test_files(s3, test_bucket, dry_run)

    print("  Pushing package manifest to registry…")
    top_hash = _push_package(
        quilt3,
        "demo/package-grant",
        entries,
        test_bucket,
        registry_bucket,
        dry_run,
    )

    print()
    print("=" * 60)
    if dry_run:
        print("✓ DRY-RUN complete — no changes made")
    else:
        print("✓ Package seeded successfully")
        print(f"  Name:     demo/package-grant")
        print(f"  Registry: s3://{registry_bucket}")
        print(f"  Hash:     {top_hash}")
        print()
        print("Quilt+ URI:")
        print(f"  quilt+s3://{registry_bucket}#package=demo/package-grant@{top_hash}")
    print("=" * 60)


if __name__ == "__main__":
    main()
