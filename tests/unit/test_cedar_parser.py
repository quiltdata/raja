import pytest

from raja.cedar.parser import parse_policy


def test_parse_policy_permit():
    policy = (
        'permit(principal == User::"alice", action == Action::"s3:GetObject", '
        'resource == S3Object::"analytics-data/report.csv");'
    )
    parsed = parse_policy(policy)
    assert parsed.effect == "permit"
    assert parsed.principal == 'User::"alice"'
    assert parsed.action == 'Action::"s3:GetObject"'
    assert parsed.resource == 'S3Object::"analytics-data/report.csv"'


def test_parse_policy_missing_fields():
    policy = 'permit(principal == User::"alice", action == Action::"read");'
    with pytest.raises(ValueError):
        parse_policy(policy)
