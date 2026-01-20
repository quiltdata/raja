from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .cedar.entities import parse_entity
from .cedar.parser import ParsedPolicy, parse_policy
from .scope import format_scope

_TEMPLATE_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _template_context() -> dict[str, str]:
    context = {
        "account": os.environ.get("AWS_ACCOUNT_ID")
        or os.environ.get("CDK_DEFAULT_ACCOUNT")
        or os.environ.get("RAJA_ACCOUNT_ID", ""),
        "region": os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("CDK_DEFAULT_REGION", ""),
        "env": os.environ.get("RAJA_ENV") or os.environ.get("ENV", ""),
    }

    disable_outputs = os.environ.get("RAJA_DISABLE_OUTPUT_CONTEXT", "").lower() in {
        "1",
        "true",
        "yes",
    }
    if (not context["account"] or not context["region"]) and not disable_outputs:
        bucket = os.environ.get("RAJEE_TEST_BUCKET") or _load_test_bucket_from_outputs()
        if bucket:
            match = re.search(r"-(\d{12})-([a-z0-9-]+)$", bucket)
            if match:
                if not context["account"]:
                    context["account"] = match.group(1)
                if not context["region"]:
                    context["region"] = match.group(2)

    return context


def _load_test_bucket_from_outputs() -> str | None:
    repo_root = Path(__file__).resolve().parents[2]
    output_paths = (
        repo_root / "infra" / "cdk-outputs.json",
        repo_root / "cdk-outputs.json",
        repo_root / "infra" / "cdk.out" / "outputs.json",
    )
    for path in output_paths:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text())
        except json.JSONDecodeError:
            continue
        bucket = _extract_output_value(payload, "TestBucketName")
        if bucket:
            return bucket
    return None


def _extract_output_value(payload: object, key: str) -> str | None:
    if isinstance(payload, dict):
        value = payload.get(key)
        if isinstance(value, str):
            return value
        for nested in payload.values():
            result = _extract_output_value(nested, key)
            if result:
                return result
    return None


def _expand_templates(value: str) -> str:
    if re.search(r"\}\}\s*\{\{", value):
        raise ValueError("template variables must be separated in bucket identifiers")
    context = _template_context()

    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        replacement = context.get(key)
        if not replacement:
            raise ValueError(f"template variable '{key}' is not set")
        return replacement

    expanded = _TEMPLATE_RE.sub(replace, value)
    if "{{" in expanded or "}}" in expanded:
        raise ValueError("unresolved template placeholders in bucket identifier")
    return expanded


def _validate_bucket_id(bucket_id: str) -> None:
    if "/" in bucket_id:
        raise ValueError("bucket identifiers must not include '/'")
    if bucket_id.endswith("-"):
        raise ValueError("bucket identifiers must be exact (no trailing '-')")
    if "{{" in bucket_id or "}}" in bucket_id:
        raise ValueError("bucket identifiers must be fully specified")


def _principal_id(policy: ParsedPolicy) -> str:
    _, principal_id = parse_entity(policy.principal)
    return principal_id


def _compile_scopes(policy: ParsedPolicy) -> list[str]:
    actions = policy.actions
    if not actions:
        raise ValueError("policy must include at least one action")

    resource_type = policy.resource_type
    resource_id = policy.resource_id

    if resource_type == "S3Object":
        if not policy.parent_ids:
            raise ValueError("S3Object policies must include a parent S3Bucket")
        if "{{" in resource_id or "}}" in resource_id:
            raise ValueError("template placeholders are only allowed in bucket identifiers")
        scopes: list[str] = []
        for parent_id in policy.parent_ids:
            bucket_id = _expand_templates(parent_id)
            _validate_bucket_id(bucket_id)
            combined_id = f"{bucket_id}/{resource_id}"
            for action in actions:
                scopes.append(format_scope(resource_type, combined_id, action))
        return scopes

    if resource_type == "S3Bucket":
        if policy.parent_ids:
            raise ValueError("S3Bucket policies must not include parent constraints")
        bucket_id = _expand_templates(resource_id)
        _validate_bucket_id(bucket_id)
        return [format_scope(resource_type, bucket_id, action) for action in actions]

    if policy.parent_ids:
        raise ValueError("resource parent constraints are not supported for this type")
    return [format_scope(resource_type, resource_id, action) for action in actions]


def compile_policy(cedar_policy: str) -> dict[str, list[str]]:
    """Compile a Cedar policy statement into a principal-to-scopes mapping."""
    parsed = parse_policy(cedar_policy)
    if parsed.effect != "permit":
        raise ValueError("forbid policies are not supported")

    principal = _principal_id(parsed)
    scopes = _compile_scopes(parsed)
    return {principal: scopes}


def compile_policies(policies: list[str]) -> dict[str, list[str]]:
    """Compile multiple policies into a merged principal-to-scopes mapping."""
    compiled: dict[str, list[str]] = {}
    for policy in policies:
        mapping = compile_policy(policy)
        for principal, scopes in mapping.items():
            if principal not in compiled:
                compiled[principal] = []
            for scope in scopes:
                if scope not in compiled[principal]:
                    compiled[principal].append(scope)
    return compiled
