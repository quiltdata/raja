import pytest

from raja.compiler import compile_policies, compile_policy


def test_compile_policy_permit():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    compiled = compile_policy(policy)
    assert compiled == {"alice": ["S3Object:analytics-data/report.csv:s3:GetObject"]}


def test_compile_policy_forbid_ignored():
    policy = (
        'forbid(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    compiled = compile_policy(policy)
    assert compiled == {}


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
    with pytest.raises(ValueError):
        compile_policy(policy)
