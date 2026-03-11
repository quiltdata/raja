#!/usr/bin/env python3
"""Seed quilt3 packages into raja-poc-registry using files from raja-poc-test."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

SEED_FILES: dict[str, bytes] = {
    "data.csv": b"col1,col2,col3\nalpha,1,true\nbeta,2,false\ngamma,3,true\n",
    "README.md": (
        b"# Package Demo\n\nSample dataset for RAJA package-grant integration tests.\n\n"
        b"## Files\n\n- `data.csv` \xe2\x80\x94 tabular data\n"
        b"- `results.json` \xe2\x80\x94 summary stats\n"
    ),
    "results.json": b'{"status": "ok", "row_count": 3, "columns": ["col1", "col2", "col3"]}\n',
}


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
    files: list[tuple[str, str, bytes]] = [
        (
            "data.csv",
            "rajee-integration/package-demo/data.csv",
            SEED_FILES["data.csv"],
        ),
        (
            "README.md",
            "rajee-integration/package-demo/README.md",
            SEED_FILES["README.md"],
        ),
        (
            "results.json",
            "rajee-integration/package-demo/results.json",
            SEED_FILES["results.json"],
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
                    Bucket=bucket,
                    Key=key,
                    Body=body,
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

    # Use lower-level build to avoid workflow-config dependency in bare registries.
    top_hash = pkg._build(  # type: ignore[attr-defined]
        name=package_name,
        registry=f"s3://{registry_bucket}",
        message=None,
    )
    return str(top_hash)


def _ensure_registry_workflow_config(s3: object, registry_bucket: str, dry_run: bool) -> None:
    key = ".quilt/workflows/config.yml"
    body = (
        b"version: '1'\n"
        b"is_workflow_required: false\n"
        b"workflows:\n"
        b"  noop:\n"
        b"    name: No-op\n"
    )
    if dry_run:
        print(f"  [DRY-RUN] Would ensure s3://{registry_bucket}/{key}")
        return
    try:
        s3.head_object(Bucket=registry_bucket, Key=key)  # type: ignore[attr-defined]
        print(f"  ✓ Registry workflow config exists: s3://{registry_bucket}/{key}")
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "404":
            s3.put_object(Bucket=registry_bucket, Key=key, Body=body)  # type: ignore[attr-defined]
            print(f"  ✓ Created registry workflow config: s3://{registry_bucket}/{key}")
        else:
            raise


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
    _ensure_registry_workflow_config(s3, registry_bucket, dry_run)

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
        assert top_hash is not None
        uri = f"quilt+s3://{registry_bucket}#package=demo/package-grant@{top_hash}"
        uri_file = Path(__file__).resolve().parents[1] / ".rale-test-uri"
        uri_file.write_text(uri + "\n")

        print("✓ Package seeded successfully")
        print("  Name:     demo/package-grant")
        print(f"  Registry: s3://{registry_bucket}")
        print(f"  Hash:     {top_hash}")
        print()
        print("Quilt+ URI:")
        print(f"  {uri}")
        print()
        print(f"export RALE_TEST_QUILT_URI={uri}")
        print(f"(also written to {uri_file})")
    print("=" * 60)


if __name__ == "__main__":
    main()
