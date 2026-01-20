import pytest

from raja.cedar.parser import parse_policy


def test_parse_policy_permit():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    parsed = parse_policy(policy)
    assert parsed.effect == "permit"
    assert parsed.principal == 'User::"alice"'
    assert parsed.action == 'Action::"s3:GetObject"'
    assert parsed.resource == 'S3Object::"report.csv"'
    assert parsed.resource_type == "S3Object"
    assert parsed.resource_id == "report.csv"
    assert parsed.parent_type == "S3Bucket"
    assert parsed.parent_id == "analytics-data"


def test_parse_policy_bucket_only():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:ListBucket", '
        'resource == S3Bucket::"analytics-data");'
    )
    parsed = parse_policy(policy)
    assert parsed.resource_type == "S3Bucket"
    assert parsed.resource_id == "analytics-data"
    assert parsed.parent_type is None
    assert parsed.parent_id is None


def test_parse_policy_bucket_template_allowed():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:ListBucket", '
        'resource == S3Bucket::"raja-poc-test-{{account}}-{{region}}");'
    )
    parsed = parse_policy(policy)
    assert parsed.resource_id == "raja-poc-test-{{account}}-{{region}}"


def test_parse_policy_key_template_rejected():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"{{account}}/report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    with pytest.raises(ValueError):
        parse_policy(policy)


def test_parse_policy_missing_fields():
    policy = 'permit(principal == User::"alice", action == Action::"read");'
    with pytest.raises(ValueError):
        parse_policy(policy)


def test_parse_policy_invalid_hierarchy():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in Document::"docs" };'
    )
    with pytest.raises(ValueError):
        parse_policy(policy)


def test_parse_policy_inverted_hierarchy():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Bucket::"analytics-data") '
        'when { resource in S3Object::"report.csv" };'
    )
    with pytest.raises(ValueError):
        parse_policy(policy)


def test_parse_policy_supports_principal_in_clause():
    policy = (
        'permit(principal in Role::"data-engineers", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    parsed = parse_policy(policy)
    assert parsed.principal == 'Role::"data-engineers"'


def test_parse_policy_supports_action_in_clause():
    policy = (
        'permit(principal == User::"alice", action in [Action::"s3:GetObject", '
        'Action::"s3:PutObject"], resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" };'
    )
    parsed = parse_policy(policy)
    assert parsed.action == 'Action::"s3:GetObject", Action::"s3:PutObject"'


def test_parse_policy_supports_multiple_in_clauses():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv") '
        'when { resource in S3Bucket::"analytics-data" || '
        'resource in S3Bucket::"raw-data" };'
    )
    parsed = parse_policy(policy)
    assert parsed.parent_id == "analytics-data"


def test_parse_policy_supports_complex_when_clauses():
    policy = """
    permit(
        principal == User::"alice",
        action == Action::"s3:GetObject",
        resource == S3Object::"report.csv"
    ) when { resource in S3Bucket::"analytics-data" && context.time < "2024-12-31" };
    """
    parsed = parse_policy(policy)
    assert parsed.resource_id == "report.csv"
