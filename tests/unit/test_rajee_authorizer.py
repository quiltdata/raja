import pytest

from raja.rajee.authorizer import construct_request_string, extract_bearer_token, is_authorized


@pytest.mark.unit
def test_extract_bearer_token() -> None:
    assert extract_bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"


@pytest.mark.unit
def test_extract_bearer_token_missing() -> None:
    with pytest.raises(ValueError):
        extract_bearer_token("")


@pytest.mark.unit
def test_extract_bearer_token_invalid_prefix() -> None:
    with pytest.raises(ValueError):
        extract_bearer_token("Token abc")


@pytest.mark.unit
def test_construct_get_object_request() -> None:
    assert construct_request_string("GET", "/bucket/key.txt", {}) == "s3:GetObject/bucket/key.txt"


@pytest.mark.unit
def test_construct_put_object_request() -> None:
    assert (
        construct_request_string("PUT", "/bucket/uploads/file.txt", {})
        == "s3:PutObject/bucket/uploads/file.txt"
    )


@pytest.mark.unit
def test_construct_delete_object_request() -> None:
    assert (
        construct_request_string("DELETE", "/bucket/uploads/file.txt", {})
        == "s3:DeleteObject/bucket/uploads/file.txt"
    )


@pytest.mark.unit
def test_construct_head_object_request() -> None:
    assert (
        construct_request_string("HEAD", "/bucket/uploads/file.txt", {})
        == "s3:HeadObject/bucket/uploads/file.txt"
    )


@pytest.mark.unit
def test_construct_list_bucket_request() -> None:
    assert construct_request_string("GET", "/bucket", {"list-type": "2"}) == "s3:ListBucket/bucket/"


@pytest.mark.unit
def test_construct_list_bucket_request_without_query() -> None:
    assert construct_request_string("GET", "/bucket", {}) == "s3:ListBucket/bucket/"


@pytest.mark.unit
def test_construct_unsupported_method() -> None:
    with pytest.raises(ValueError):
        construct_request_string("POST", "/bucket/key.txt", {})


@pytest.mark.unit
def test_prefix_authorization_match() -> None:
    grants = ["s3:GetObject/bucket/uploads/"]
    assert is_authorized("s3:GetObject/bucket/uploads/file.txt", grants)


@pytest.mark.unit
def test_prefix_authorization_no_match() -> None:
    grants = ["s3:GetObject/bucket/uploads/"]
    assert not is_authorized("s3:GetObject/bucket/docs/file.txt", grants)
