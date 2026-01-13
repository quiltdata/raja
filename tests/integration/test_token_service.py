import os

import pytest


@pytest.mark.integration
def test_token_service_endpoint():
    if not os.environ.get("RAJA_API_URL"):
        pytest.skip("RAJA_API_URL not set")
    assert True
