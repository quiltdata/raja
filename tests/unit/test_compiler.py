import os
import shutil

import pytest

from raja.compiler import compile_policies, compile_policy


def _cedar_tool_available() -> bool:
    return bool(shutil.which("cargo")) or bool(os.environ.get("CEDAR_PARSE_BIN"))


pytestmark = pytest.mark.skipif(
    not _cedar_tool_available(), reason="cargo or CEDAR_PARSE_BIN is required for Cedar parsing"
)


def test_compile_policy_permit():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    compiled = compile_policy(policy)
    assert compiled == {"alice": ["S3Object:analytics-data/report.csv:s3:GetObject"]}


def test_compile_policy_forbid_rejected():
    policy = (
        'forbid(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    with pytest.raises(ValueError):
        compile_policy(policy)


def test_compile_policies_aggregates():
    policies = [
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        (
            'permit(principal == User::"alice", action == Action::"s3:PutObject", '
            'resource == S3Object::"upload.csv") '
            'when { resource in S3Bucket::"raw-data" };'
        ),
    ]
    compiled = compile_policies(policies)
    assert compiled == {
        "alice": [
            "S3Object:analytics-data/report.csv:s3:GetObject",
            "S3Object:raw-data/upload.csv:s3:PutObject",
        ]
    }


def test_compile_policy_expands_bucket_templates(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:ListBucket", '
        'resource == S3Bucket::"raja-poc-test-{{account}}-{{region}}");'
    )
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    monkeypatch.setenv("AWS_REGION", "us-west-2")
    compiled = compile_policy(policy)
    assert compiled == {"alice": ["S3Bucket:raja-poc-test-123456789012-us-west-2:s3:ListBucket"]}


def test_compile_policy_rejects_missing_template_values(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:ListBucket", '
        'resource == S3Bucket::"raja-poc-test-{{account}}-{{region}}");'
    )
    monkeypatch.delenv("AWS_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("AWS_REGION", raising=False)
    monkeypatch.setenv("RAJA_DISABLE_OUTPUT_CONTEXT", "1")
    with pytest.raises(ValueError):
        compile_policy(policy)


def test_compile_policy_rejects_double_template_expansion(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:ListBucket", '
        'resource == S3Bucket::"raja-poc-test-{{account}}{{account}}");'
    )
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    with pytest.raises(ValueError):
        compile_policy(policy)


def test_compile_policy_rejects_template_in_object_key(monkeypatch: pytest.MonkeyPatch) -> None:
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"{{account}}/report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    monkeypatch.setenv("AWS_ACCOUNT_ID", "123456789012")
    with pytest.raises(ValueError):
        compile_policy(policy)


def test_compile_policy_rejects_complex_when_clause() -> None:
    policy = """
    permit(
        principal == User::"alice",
        action == Action::"s3:GetObject",
        resource == S3Object::"report.csv"
    ) when { resource in S3Bucket::"analytics-data" && context.time < "2024-12-31" };
    """
    with pytest.raises(ValueError):
        compile_policy(policy)


def test_compile_policy_supports_action_in_clause() -> None:
    policy = """
    permit(
        principal == User::"alice",
        action in [Action::"s3:GetObject", Action::"s3:PutObject"],
        resource == S3Object::"report.csv"
    ) when { resource in S3Bucket::"analytics-data" };
    """
    compiled = compile_policy(policy)
    assert compiled == {
        "alice": [
            "S3Object:analytics-data/report.csv:s3:GetObject",
            "S3Object:analytics-data/report.csv:s3:PutObject",
        ]
    }


def test_compile_policy_supports_principal_in_clause() -> None:
    policy = """
    permit(
        principal in Role::"data-engineers",
        action == Action::"s3:GetObject",
        resource == S3Object::"report.csv"
    ) when { resource in S3Bucket::"analytics-data" };
    """
    compiled = compile_policy(policy)
    assert compiled == {
        "data-engineers": [
            "S3Object:analytics-data/report.csv:s3:GetObject",
        ]
    }


def test_compile_policy_supports_multiple_in_clauses() -> None:
    policy = """
    permit(
        principal == User::"alice",
        action == Action::"s3:GetObject",
        resource == S3Object::"report.csv"
    ) when {
        resource in S3Bucket::"analytics-data" ||
        resource in S3Bucket::"raw-data"
    };
    """
    compiled = compile_policy(policy)
    assert compiled == {
        "alice": [
            "S3Object:analytics-data/report.csv:s3:GetObject",
            "S3Object:raw-data/report.csv:s3:GetObject",
        ]
    }


def test_compile_policies_deduplicates_scopes() -> None:
    policies = [
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
        (
            'permit(principal == User::"alice", action == Action::"s3:GetObject", '
            'resource == S3Object::"report.csv") '
            'when { resource in S3Bucket::"analytics-data" };'
        ),
    ]
    compiled = compile_policies(policies)
    assert compiled == {"alice": ["S3Object:analytics-data/report.csv:s3:GetObject"]}
