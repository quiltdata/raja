import pytest

from raja.quilt_uri import normalize_quilt_uri, package_name_matches, parse_quilt_uri


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


@pytest.mark.parametrize(
    ("pattern", "name", "expected"),
    [
        ("exp*", "experiment-01", True),
        ("experiment/*", "experiment/run1", True),
        ("experiment/*", "experiment", False),
        ("data/*/v2", "data/project/v2", True),
        ("data/*/v2", "data/project/v1", False),
    ],
)
def test_package_name_matches(pattern: str, name: str, expected: bool) -> None:
    assert package_name_matches(pattern, name) is expected
