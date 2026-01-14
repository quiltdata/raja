import pytest

from raja.cedar.schema import CedarSchema
from raja.models import CedarPolicy


def test_validate_policy_accepts_known_resource_and_action():
    schema = CedarSchema(resource_types={"Document"}, actions={"read"})
    policy = CedarPolicy(
        effect="permit",
        principal='User::"alice"',
        action='Action::"read"',
        resource='Document::"doc1"',
    )
    schema.validate_policy(policy)


def test_validate_policy_rejects_unknown_resource():
    schema = CedarSchema(resource_types={"Document"}, actions={"read"})
    policy = CedarPolicy(
        effect="permit",
        principal='User::"alice"',
        action='Action::"read"',
        resource='Photo::"doc1"',
    )
    with pytest.raises(ValueError):
        schema.validate_policy(policy)


def test_validate_policy_rejects_unknown_action():
    schema = CedarSchema(resource_types={"Document"}, actions={"read"})
    policy = CedarPolicy(
        effect="permit",
        principal='User::"alice"',
        action='Action::"write"',
        resource='Document::"doc1"',
    )
    with pytest.raises(ValueError):
        schema.validate_policy(policy)
