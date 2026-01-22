#!/usr/bin/env python3
"""Load Cedar policy files to AWS Verified Permissions."""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError

_TEMPLATE_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _template_context() -> dict[str, str]:
    context = {
        "account": os.environ.get("AWS_ACCOUNT_ID") or os.environ.get("CDK_DEFAULT_ACCOUNT") or "",
        "region": os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("CDK_DEFAULT_REGION")
        or "",
        "env": os.environ.get("RAJA_ENV") or os.environ.get("ENV") or "",
    }

    if not context["account"]:
        try:
            context["account"] = boto3.client("sts").get_caller_identity()["Account"]
        except Exception:
            pass

    if not context["region"]:
        region = boto3.session.Session().region_name
        if region:
            context["region"] = region

    return context


def _expand_templates(statement: str) -> str:
    context = _template_context()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        value = context.get(key)
        if not value:
            raise ValueError(f"template variable '{key}' is not set")
        return value

    expanded = _TEMPLATE_RE.sub(replace, statement)
    if "{{" in expanded or "}}" in expanded:
        raise ValueError("unresolved template placeholders in policy statement")
    return expanded


def _split_statements(policy_text: str) -> list[str]:
    """Split a Cedar policy file into individual statements."""
    statements: list[str] = []
    for chunk in policy_text.split(";"):
        statement = chunk.strip()
        if statement:
            statements.append(f"{statement};")
    return statements


def _normalize_statement(statement: str) -> str:
    normalized = statement.strip()
    return _expand_templates(normalized) if "{{" in normalized else normalized


def _load_policy_files(policies_dir: Path) -> list[str]:
    """Load all .cedar policy files from directory."""
    policy_files = sorted(
        path for path in policies_dir.glob("*.cedar") if path.name != "schema.cedar"
    )

    if not policy_files:
        print(f"⚠ No .cedar files found in {policies_dir}")
        sys.exit(1)

    policies: list[str] = []
    for path in policy_files:
        policy_text = path.read_text(encoding="utf-8")
        policies.extend(_split_statements(policy_text))
    return policies


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


def _list_policies(client: Any, policy_store_id: str) -> list[dict[str, Any]]:
    policies: list[dict[str, Any]] = []
    next_token: str | None = None
    while True:
        kwargs = {"policyStoreId": policy_store_id, "maxResults": 100}
        if next_token:
            kwargs["nextToken"] = next_token
        response = client.list_policies(**kwargs)
        policies.extend(response.get("policies", []))
        next_token = response.get("nextToken")
        if not next_token:
            break
    return policies


def _get_policy_statement(client: Any, policy_store_id: str, policy_id: str) -> str | None:
    response = client.get_policy(policyStoreId=policy_store_id, policyId=policy_id)
    definition = response.get("definition", {})
    static_def = definition.get("static", {})
    statement = static_def.get("statement")
    if not isinstance(statement, str):
        return None
    return _normalize_statement(statement)


def _delete_policy(client: Any, policy_store_id: str, policy_id: str, dry_run: bool) -> None:
    if dry_run:
        print(f"  [DRY-RUN] Would delete policy: {policy_id}")
        return
    client.delete_policy(policyStoreId=policy_store_id, policyId=policy_id)
    print("✓ Deleted policy")


def main() -> None:
    """Load Cedar policies to AWS Verified Permissions."""
    # Parse arguments
    dry_run = "--dry-run" in sys.argv

    # Get configuration
    policy_store_id = os.environ.get("POLICY_STORE_ID")
    if not policy_store_id:
        repo_root = Path(__file__).resolve().parents[1]
        outputs_path = repo_root / "infra" / "cdk-outputs.json"
        if outputs_path.is_file():
            try:
                import json

                outputs = json.loads(outputs_path.read_text())
                policy_store_id = (
                    outputs.get("RajaAvpStack", {}).get("PolicyStoreId") or policy_store_id
                )
            except json.JSONDecodeError:
                pass
    if not policy_store_id:
        print("✗ POLICY_STORE_ID environment variable is required", file=sys.stderr)
        sys.exit(1)

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION")
    if not region:
        region = boto3.session.Session().region_name
    if not region:
        repo_root = Path(__file__).resolve().parents[1]
        outputs_path = repo_root / "infra" / "cdk-outputs.json"
        if outputs_path.is_file():
            try:
                import json

                outputs = json.loads(outputs_path.read_text())
                api_url = outputs.get("RajaServicesStack", {}).get("ApiUrl")
                if isinstance(api_url, str):
                    host = urlparse(api_url).hostname or ""
                    parts = host.split(".")
                    if "execute-api" in parts:
                        region = parts[2] if len(parts) > 2 else None
            except json.JSONDecodeError:
                pass
    if not region:
        print("✗ AWS_REGION environment variable is required", file=sys.stderr)
        sys.exit(1)
    os.environ.setdefault("AWS_REGION", region)

    # Load policies
    repo_root = Path(__file__).resolve().parents[1]
    policies_dir = repo_root / "policies"

    if not policies_dir.exists():
        print(f"✗ Policies directory not found: {policies_dir}", file=sys.stderr)
        sys.exit(1)

    policies = [_normalize_statement(p) for p in _load_policy_files(policies_dir)]
    if len(set(policies)) != len(policies):
        print("⚠ Duplicate policy statements detected; deduplicating.")
        policies = sorted(set(policies))

    print(f"{'='*60}")
    print(f"Loading {len(policies)} policies to AVP")
    print(f"Policy Store: {policy_store_id}")
    print(f"Region: {region or 'default'}")
    if dry_run:
        print("Mode: DRY-RUN (no changes will be made)")
    print(f"{'='*60}\n")

    # Create client
    try:
        client = boto3.client("verifiedpermissions", region_name=region)
    except Exception as e:
        print(f"✗ Failed to create AWS client: {e}", file=sys.stderr)
        sys.exit(1)

    # Reconcile policies to match local statements
    success_count = 0
    skip_count = 0
    delete_count = 0
    fail_count = 0

    desired = list(policies)
    desired_set = set(desired)
    matched_statements: set[str] = set()

    existing = _list_policies(client, policy_store_id)
    for policy in existing:
        policy_id = policy.get("policyId")
        if not policy_id:
            continue
        try:
            statement = _get_policy_statement(client, policy_store_id, policy_id)
            if not statement:
                _delete_policy(client, policy_store_id, policy_id, dry_run)
                delete_count += 1
                continue
            if statement in desired_set and statement not in matched_statements:
                matched_statements.add(statement)
                skip_count += 1
            else:
                _delete_policy(client, policy_store_id, policy_id, dry_run)
                delete_count += 1
        except Exception as e:
            print(f"  Unexpected error: {e}")
            fail_count += 1

    remaining = [s for s in desired if s not in matched_statements]
    for i, statement in enumerate(remaining, 1):
        print(f"[{i}/{len(remaining)}] Creating policy...")
        try:
            _create_policy(client, policy_store_id, statement, dry_run)
            success_count += 1
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConflictException":
                skip_count += 1
            else:
                fail_count += 1
        except Exception as e:
            print(f"  Unexpected error: {e}")
            fail_count += 1

    print(f"\n{'='*60}")
    if dry_run:
        print(f"✓ DRY-RUN: Would load {len(policies)} policies")
    else:
        print(f"✓ Created {success_count}/{len(remaining)} policies successfully")
        if skip_count > 0:
            print(f"⚠ Skipped {skip_count} unchanged policies")
        if delete_count > 0:
            print(f"⚠ Deleted {delete_count} stale policies")
        if fail_count > 0:
            print(f"✗ Failed to load {fail_count} policies")
    print(f"{'='*60}")

    if fail_count > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
