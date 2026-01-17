import pytest

from raja.cedar.parser import parse_policy


def test_parse_policy_permit():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv" in S3Bucket::"analytics-data");'
    )
    parsed = parse_policy(policy)
    assert parsed.effect == "permit"
    assert parsed.principal == 'User::"alice"'
    assert parsed.action == 'Action::"s3:GetObject"'
    assert parsed.resource == 'S3Object::"report.csv" in S3Bucket::"analytics-data"'
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


def test_parse_policy_missing_fields():
    policy = 'permit(principal == User::"alice", action == Action::"read");'
    with pytest.raises(ValueError):
        parse_policy(policy)


def test_parse_policy_invalid_hierarchy():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"report.csv" in Document::"docs");'
    )
    with pytest.raises(ValueError):
        parse_policy(policy)
