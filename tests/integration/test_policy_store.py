import os

import boto3
import pytest


@pytest.mark.integration
def test_policy_store_lists_policies():
    policy_store_id = os.environ.get("POLICY_STORE_ID")
    if not policy_store_id:
        pytest.skip("POLICY_STORE_ID not set")

    client = boto3.client("verifiedpermissions")
    response = client.list_policies(policyStoreId=policy_store_id, maxResults=10)
    policies = response.get("policies", [])
    assert len(policies) >= 1
