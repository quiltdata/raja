from __future__ import annotations

import json
import os
import re
from pathlib import Path

from .cedar.entities import parse_entity
from .cedar.parser import ParsedPolicy, parse_policy
from .quilt_uri import parse_quilt_uri
from .scope import format_scope

_TEMPLATE_RE = re.compile(r"\{\{([a-zA-Z0-9_]+)\}\}")


def _template_context() -> dict[str, str]:
    """Get template variable context from environment.

    Supported variables:
    - account: AWS account ID
    - region: AWS region
    - env: Environment name
    """
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
    """Load test bucket name from CDK outputs."""
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
    """Extract value from nested CDK outputs dict."""
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
    """Expand template variables in string.

    Template format: {{variable_name}}

    Supported variables:
    - {{account}} - AWS account ID
    - {{region}} - AWS region
    - {{env}} - Environment name
    """
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
    """Validate bucket identifier format."""
    if "/" in bucket_id:
        raise ValueError("bucket identifiers must not include '/'")
    if bucket_id.endswith("-"):
        raise ValueError("bucket identifiers must be exact (no trailing '-')")
    if "{{" in bucket_id or "}}" in bucket_id:
        raise ValueError("bucket identifiers must be fully specified")


def _principal_id(policy: ParsedPolicy) -> str:
    """Extract principal ID from parsed policy."""
    _, principal_id = parse_entity(policy.principal)
    return principal_id


def _parse_package_identifier(resource_id: str) -> tuple[str, str]:
    """Extract package name and hash from a resource identifier."""
    if resource_id.startswith("quilt+"):
        parsed = parse_quilt_uri(resource_id)
        return parsed.package_name, parsed.hash
    if "@" not in resource_id:
        raise ValueError("package resource must include an immutable hash")
    package_name, package_hash = resource_id.rsplit("@", 1)
    if not package_name or not package_hash:
        raise ValueError("package resource must include name and hash")
    return package_name, package_hash


def _compile_scopes(policy: ParsedPolicy) -> list[str]:
    """Compile parsed policy to scope strings.

    Handles:
    - S3Object with parent bucket constraints
    - S3Bucket resources
    - Template variable expansion
    - Multiple actions
    """
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

    if resource_type == "Package":
        if policy.parent_ids:
            raise ValueError("Package policies must not include parent constraints")
        package_name, package_hash = _parse_package_identifier(resource_id)
        package_id = f"{package_name}@{package_hash}"
        return [format_scope(resource_type, package_id, action) for action in actions]

    if policy.parent_ids:
        raise ValueError("resource parent constraints are not supported for this type")
    return [format_scope(resource_type, resource_id, action) for action in actions]


def compile_policy(cedar_policy: str, schema_path: str | None = None) -> dict[str, list[str]]:
    """Compile a Cedar policy statement into a principal-to-scopes mapping.

    Supports:
    - permit policies (compiled to scopes)
    - forbid policies (compiled to negative scopes - NOT YET IMPLEMENTED)

    Args:
        cedar_policy: Cedar policy text
        schema_path: Optional path to Cedar schema for validation

    Returns:
        Dict mapping principal ID to list of scope strings

    Raises:
        ValueError: If policy is invalid or unsupported
    """
    parsed = parse_policy(cedar_policy, schema_path)

    # Phase 3: Basic forbid support - reject forbid policies for now
    # TODO: Implement forbid scope exclusion in compile_policies()
    if parsed.effect == "forbid":
        raise ValueError("forbid policies are not yet fully supported")

    principal = _principal_id(parsed)
    scopes = _compile_scopes(parsed)
    return {principal: scopes}


def compile_policies(
    policies: list[str], schema_path: str | None = None, handle_forbids: bool = False
) -> dict[str, list[str]]:
    """Compile multiple policies into a merged principal-to-scopes mapping.

    Phase 3: Forbid Policy Support
    - Compiles permit policies to scopes
    - Compiles forbid policies to exclusion scopes
    - Excludes forbidden scopes from permits (when handle_forbids=True)

    Args:
        policies: List of Cedar policy strings
        schema_path: Optional path to Cedar schema for validation
        handle_forbids: If True, handle forbid policies via scope exclusion

    Returns:
        Dict mapping principal ID to list of granted scope strings
        (with forbidden scopes excluded if handle_forbids=True)

    Raises:
        ValueError: If policies are invalid
    """
    permits: dict[str, list[str]] = {}
    forbids: dict[str, list[str]] = {}

    for policy in policies:
        parsed = parse_policy(policy, schema_path)
        principal = _principal_id(parsed)
        scopes = _compile_scopes(parsed)

        if parsed.effect == "permit":
            if principal not in permits:
                permits[principal] = []
            for scope in scopes:
                if scope not in permits[principal]:
                    permits[principal].append(scope)
        elif parsed.effect == "forbid":
            if not handle_forbids:
                raise ValueError(
                    "forbid policies are not yet fully supported "
                    "(set handle_forbids=True to enable)"
                )
            if principal not in forbids:
                forbids[principal] = []
            for scope in scopes:
                if scope not in forbids[principal]:
                    forbids[principal].append(scope)

    # Phase 3: Apply forbid exclusions
    if handle_forbids and forbids:
        compiled: dict[str, list[str]] = {}
        for principal, permit_scopes in permits.items():
            forbidden_scopes = forbids.get(principal, [])
            # Exclude forbidden scopes from permits
            granted_scopes = [scope for scope in permit_scopes if scope not in forbidden_scopes]
            if granted_scopes:
                compiled[principal] = granted_scopes
        return compiled

    return permits


def expand_wildcard_actions(action_pattern: str, resource_type: str) -> list[str]:
    """Expand wildcard action patterns to concrete actions.

    Phase 4: Action Hierarchy Support

    Examples:
    - s3:* → ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", ...]
    - s3:Get* → ["s3:GetObject", "s3:GetObjectAcl", ...]

    Args:
        action_pattern: Action pattern (may contain *)
        resource_type: Resource type for context-aware expansion

    Returns:
        List of concrete action names

    Raises:
        ValueError: If pattern is invalid or unsupported
    """
    # Phase 4: TODO - Implement action hierarchy expansion
    # For now, return the pattern as-is if no wildcard, or raise error
    if "*" in action_pattern:
        raise ValueError(f"wildcard action patterns are not yet supported: {action_pattern}")
    return [action_pattern]


def instantiate_policy_template(
    template: str, variables: dict[str, str], schema_path: str | None = None
) -> dict[str, list[str]]:
    """Instantiate a Cedar policy template with variable values.

    Phase 4: Template Instantiation

    Supported template variables:
    - {{principal}} - Principal identifier
    - {{user}} - User identifier (alias for principal)
    - {{bucket}} - Bucket identifier
    - {{resource}} - Resource identifier
    - {{action}} - Action identifier

    Example:
        template = '''
        permit(
            principal == User::"{{user}}",
            action == Action::"{{action}}",
            resource == S3Bucket::"{{bucket}}"
        );
        '''

        variables = {
            "user": "alice",
            "action": "s3:ListBucket",
            "bucket": "my-bucket"
        }

        result = instantiate_policy_template(template, variables)
        # Returns: {"alice": ["S3Bucket:my-bucket:s3:ListBucket"]}

    Args:
        template: Cedar policy template string
        variables: Dict of variable names to values
        schema_path: Optional path to Cedar schema for validation

    Returns:
        Dict mapping principal ID to list of scope strings

    Raises:
        ValueError: If template is invalid or variables are missing
    """
    # Expand template variables in policy text
    policy_text = template
    for var_name, var_value in variables.items():
        placeholder = f"{{{{{var_name}}}}}"
        policy_text = policy_text.replace(placeholder, var_value)

    # Check for unresolved variables
    unresolved = re.findall(r"\{\{([^}]+)\}\}", policy_text)
    if unresolved:
        raise ValueError(f"unresolved template variables: {', '.join(unresolved)}")

    # Compile the instantiated policy
    return compile_policy(policy_text, schema_path)
