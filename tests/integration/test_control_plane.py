import pytest

from .helpers import request_json


@pytest.mark.integration
def test_control_plane_compiles_policies():
    status, body = request_json("POST", "/compile")
    assert status == 200
    assert body.get("policies_compiled", 0) >= 1


@pytest.mark.integration
def test_control_plane_lists_principals():
    status, body = request_json("GET", "/principals")
    assert status == 200
    principals = {item.get("principal") for item in body.get("principals", [])}
    assert {"alice", "admin"}.issubset(principals)


@pytest.mark.integration
def test_control_plane_lists_policies():
    status, body = request_json("GET", "/policies")
    assert status == 200
    assert len(body.get("policies", [])) >= 1
