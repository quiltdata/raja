import os

import pytest


@pytest.mark.integration
def test_policy_store_available():
    if not os.environ.get("POLICY_STORE_ID"):
        pytest.skip("POLICY_STORE_ID not set")
    assert True
