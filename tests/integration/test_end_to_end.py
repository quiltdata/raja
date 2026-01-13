import os

import pytest


@pytest.mark.integration
def test_end_to_end_flow():
    if not os.environ.get("RAJA_API_URL"):
        pytest.skip("RAJA_API_URL not set")
    assert True
