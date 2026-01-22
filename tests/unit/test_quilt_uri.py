import pytest

from raja.quilt_uri import normalize_quilt_uri, parse_quilt_uri


def test_parse_quilt_uri_basic() -> None:
    uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    parsed = parse_quilt_uri(uri)

    assert parsed.storage == "s3"
    assert parsed.registry == "registry"
    assert parsed.package_name == "my/pkg"
    assert parsed.hash == "abc123def456"
    assert parsed.path is None


def test_parse_quilt_uri_with_path() -> None:
    uri = "quilt+s3://registry#package=my/pkg@abc123def456&path=data/file.csv"
    parsed = parse_quilt_uri(uri)

    assert parsed.path == "data/file.csv"


def test_normalize_quilt_uri() -> None:
    uri = "Quilt+S3://registry/#package=my/pkg@abc123def456&path=data\\file.csv"
    normalized = normalize_quilt_uri(uri)

    assert normalized == "quilt+s3://registry#package=my/pkg@abc123def456&path=data/file.csv"


@pytest.mark.parametrize(
    "uri",
    [
        "quilt+s3://registry#package=my/pkg",
        "quilt+s3://registry#path=data/file.csv",
        "s3://registry#package=my/pkg@abc123def456",
    ],
)
def test_parse_quilt_uri_invalid(uri: str) -> None:
    with pytest.raises(ValueError):
        parse_quilt_uri(uri)
