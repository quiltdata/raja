#!/usr/bin/env python3
"""Seed Quilt packages and DataZone grants from seed-config.yaml."""

from __future__ import annotations

import os
from pathlib import Path
import sys

import boto3
from botocore.exceptions import ClientError

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from raja.datazone import DataZoneConfig, DataZoneError, DataZoneService, datazone_enabled
from scripts.seed_config import (
    DEFAULT_TEST_URI_PATH,
    load_seed_config,
    load_seed_state,
    write_seed_state,
)
from scripts.tf_outputs import get_tf_output


def seed_files_for_package(package_name: str) -> dict[str, bytes]:
    author, dataset = package_name.split("/", 1)
    topology_version = "symmetric-v2"
    readme = (
        f"# {package_name}\n\n"
        "Seeded dataset for RAJA package-grant integration tests.\n\n"
        f"- Producer project: {author}\n"
        f"- Dataset: {dataset}\n"
        f"- Topology version: {topology_version}\n"
    ).encode()
    data_csv = (
        "project,ordinal,accessible,topology\n"
        f"{author},1,true,{topology_version}\n"
        f"{author},2,false,{topology_version}\n"
        f"{author},3,true,{topology_version}\n"
    ).encode()
    results_json = (
        "{"
        f'"package": "{package_name}", '
        f'"producer_project": "{author}", '
        f'"topology_version": "{topology_version}", '
        '"row_count": 3'
        "}\n"
    ).encode()
    return {
        "data.csv": data_csv,
        "README.md": readme,
        "results.json": results_json,
    }


SEED_FILES: dict[str, bytes] = seed_files_for_package("alpha/home")


def _get_region() -> str:
    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
    if not region:
        print("✗ AWS_REGION environment variable is required", file=sys.stderr)
        sys.exit(1)
    return region


def _hydrate_datazone_env() -> None:
    mapping = {
        "DATAZONE_DOMAIN_ID": "datazone_domain_id",
        "DATAZONE_PROJECTS": "datazone_projects",
        "DATAZONE_PACKAGE_ASSET_TYPE": "datazone_package_asset_type",
        "DATAZONE_PACKAGE_ASSET_TYPE_REVISION": "datazone_package_asset_type_revision",
    }
    for env_key, output_key in mapping.items():
        if os.environ.get(env_key):
            continue
        value = get_tf_output(output_key)
        if value:
            os.environ[env_key] = value


def _get_account_id() -> str:
    return boto3.client("sts").get_caller_identity()["Account"]


def _ensure_test_files(
    s3: object,
    bucket: str,
    package_name: str,
    dry_run: bool,
) -> list[tuple[str, str]]:
    """Upload sample files to the test bucket if they don't already exist.

    Returns list of (logical_name, s3_key) tuples.
    """
    seed_files = seed_files_for_package(package_name)
    path_prefix = f"rajee-integration/{package_name.replace('/', '-')}"
    files: list[tuple[str, str, bytes]] = [
        ("data.csv", f"{path_prefix}/data.csv", seed_files["data.csv"]),
        ("README.md", f"{path_prefix}/README.md", seed_files["README.md"]),
        ("results.json", f"{path_prefix}/results.json", seed_files["results.json"]),
    ]

    entries: list[tuple[str, str]] = []
    for logical_name, key, body in files:
        entries.append((logical_name, key))
        if dry_run:
            print(f"  [DRY-RUN] Would ensure s3://{bucket}/{key}")
            continue
        s3.put_object(  # type: ignore[attr-defined]
            Bucket=bucket,
            Key=key,
            Body=body,
        )
        print(f"  ✓ Upserted: s3://{bucket}/{key}")

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
    body = b"version: '1'\nis_workflow_required: false\nworkflows:\n  noop:\n    name: No-op\n"
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
    _hydrate_datazone_env()

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

    seed_config = load_seed_config()
    package_state: dict[str, dict[str, str]] = {}

    print()
    print("=" * 60)
    if dry_run:
        print("✓ DRY-RUN complete — no changes made")
    else:
        config = DataZoneConfig.from_env() if datazone_enabled() else None
        service = (
            DataZoneService(client=boto3.client("datazone", region_name=region), config=config)
            if config is not None
            else None
        )
        project_ids = config and seed_config.project_id_map(config) or {}

        for index, package in enumerate(seed_config.packages, start=1):
            print(f"[{index}/{len(seed_config.packages)}] {package.name}")
            print("  Ensuring test files exist in test bucket...")
            entries = _ensure_test_files(s3, test_bucket, package.name, dry_run)
            print("  Pushing package manifest to registry...")
            top_hash = _push_package(
                quilt3,
                package.name,
                entries,
                test_bucket,
                registry_bucket,
                dry_run,
            )
            assert top_hash is not None
            uri = f"quilt+s3://{registry_bucket}#package={package.name}@{top_hash}"
            producer_project_id = project_ids.get(package.producer_project, "")
            consumer_project_id = project_ids.get(package.consumer_project, "")
            listing_id = ""
            if service is not None:
                if not producer_project_id or not consumer_project_id:
                    print(
                        f"✗ Missing DataZone project mapping for {package.name}: "
                        f"{package.producer_project}->{package.consumer_project}",
                        file=sys.stderr,
                    )
                    sys.exit(1)
                try:
                    listing = service.ensure_package_listing(
                        uri,
                        owner_project_id=producer_project_id,
                    )
                    listing_id = service.ensure_project_package_grant(
                        project_id=consumer_project_id,
                        quilt_uri=uri,
                        owner_project_id=producer_project_id,
                    )
                    print(f"  DataZone listing: {listing.listing_id}")
                    print(f"  Grant: {package.producer_project} -> {package.consumer_project}")
                except DataZoneError as exc:
                    print(f"✗ Failed to sync DataZone package grant: {exc}", file=sys.stderr)
                    sys.exit(1)
            package_state[package.name] = {
                "uri": uri,
                "listing_id": listing_id,
                "producer_project": package.producer_project,
                "consumer_project": package.consumer_project,
            }
            print(f"  Hash: {top_hash}")
            print(f"  URI:  {uri}")

        default_package_name = seed_config.package_for_home_project(
            seed_config.default_project
        ).name
        default_uri = package_state[default_package_name]["uri"]
        DEFAULT_TEST_URI_PATH.write_text(default_uri + "\n")

        state = load_seed_state()
        state["default_package"] = default_package_name
        state["packages"] = package_state
        existing_projects = state.get("projects", {})
        if isinstance(existing_projects, dict):
            for project in seed_config.projects:
                project_state = existing_projects.setdefault(project.key, {})
                if not isinstance(project_state, dict):
                    continue
                project_state["home_package"] = seed_config.package_for_home_project(
                    project.key
                ).name
                project_state["foreign_package"] = seed_config.package_for_consumer_project(
                    project.key
                ).name
                project_state["inaccessible_package"] = (
                    seed_config.package_for_inaccessible_project(project.key).name
                )
        write_seed_state(state)

        print("✓ Packages seeded successfully")
        print(f"  Default package: {default_package_name}")
        print(f"  Registry: s3://{registry_bucket}")
        print()
        print(f"export RALE_TEST_QUILT_URI={default_uri}")
        print(f"(also written to {DEFAULT_TEST_URI_PATH})")
    print("=" * 60)


if __name__ == "__main__":
    main()
