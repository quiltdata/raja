import pytest

from raja.cedar.schema import CedarSchema
from raja.models import CedarPolicy


def test_validate_policy_accepts_known_resource_and_action():
    schema = CedarSchema(resource_types={"S3Object"}, actions={"s3:GetObject"})
    policy = CedarPolicy(
        id="policy1",
        effect="permit",
        principal='User::"alice"',
        action='Action::"s3:GetObject"',
        resource='S3Object::"report.csv" in S3Bucket::"analytics-data"',
    )
    schema.validate_policy(policy)


def test_validate_policy_rejects_unknown_resource():
    schema = CedarSchema(resource_types={"S3Object"}, actions={"s3:GetObject"})
    policy = CedarPolicy(
        id="policy2",
        effect="permit",
        principal='User::"alice"',
        action='Action::"s3:GetObject"',
        resource='DynamoDBTable::"users" in S3Bucket::"analytics-data"',
    )
    with pytest.raises(ValueError):
        schema.validate_policy(policy)


def test_validate_policy_rejects_unknown_action():
    schema = CedarSchema(resource_types={"S3Object"}, actions={"s3:GetObject"})
    policy = CedarPolicy(
        id="policy3",
        effect="permit",
        principal='User::"alice"',
        action='Action::"s3:DeleteObject"',
        resource='S3Object::"report.csv" in S3Bucket::"analytics-data"',
    )
    with pytest.raises(ValueError):
        schema.validate_policy(policy)
