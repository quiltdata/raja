from __future__ import annotations

import os
import re

from .cedar.entities import parse_entity
from .cedar.parser import parse_policy, parse_resource_clause
from .models import CedarPolicy
from .scope import format_scope

_TEMPLATE_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _template_context() -> dict[str, str]:
    return {
        "account": os.environ.get("AWS_ACCOUNT_ID")
        or os.environ.get("CDK_DEFAULT_ACCOUNT")
        or os.environ.get("RAJA_ACCOUNT_ID", ""),
        "region": os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
        or os.environ.get("CDK_DEFAULT_REGION", ""),
        "env": os.environ.get("RAJA_ENV") or os.environ.get("ENV", ""),
    }


def _expand_templates(value: str) -> str:
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


def _action_id(action_str: str) -> str:
    try:
        _, action_id = parse_entity(action_str)
        return action_id
    except ValueError:
        return action_str.strip().strip('"')


def _principal_id(policy: CedarPolicy) -> str:
    _, principal_id = parse_entity(policy.principal)
    return principal_id


def _resource_parts(policy: CedarPolicy) -> tuple[str, str]:
    if policy.resource_type:
        resource_type = policy.resource_type
        resource_id = policy.resource_id or ""
        parent_type = policy.parent_type
        parent_id = policy.parent_id
    else:
        resource_type, resource_id, parent_type, parent_id = parse_resource_clause(policy.resource)

    if parent_type:
        if resource_type != "S3Object" or parent_type != "S3Bucket":
            raise ValueError("only S3Object in S3Bucket hierarchies are supported")
        if parent_id is None:
            raise ValueError("S3Object policies must include a parent S3Bucket")
        bucket_id = _expand_templates(parent_id)
        _validate_bucket_id(bucket_id)
        resource_id = f"{bucket_id}/{resource_id}"
    elif resource_type == "S3Bucket":
        if resource_id is None:
            raise ValueError("S3Bucket policies must include a bucket identifier")
        bucket_id = _expand_templates(resource_id)
        _validate_bucket_id(bucket_id)
        resource_id = bucket_id

    return resource_type, resource_id


def compile_policy(cedar_policy: str) -> dict[str, list[str]]:
    """Compile a Cedar policy statement into a principal-to-scopes mapping."""
    parsed = parse_policy(cedar_policy)
    if parsed.effect != "permit":
        return {}

    principal = _principal_id(parsed)
    resource_type, resource_id = _resource_parts(parsed)
    action = _action_id(parsed.action)
    scope = format_scope(resource_type, resource_id, action)
    return {principal: [scope]}


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
