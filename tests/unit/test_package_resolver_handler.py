from __future__ import annotations

from raja.models import S3Location
from raja.package_map import PackageMap


def test_resolve_manifest_delegates(monkeypatch) -> None:
    expected = [S3Location(bucket="bucket", key="data/file.csv")]

    def fake_resolve(quilt_uri: str) -> list[S3Location]:
        assert quilt_uri == "quilt+s3://registry#package=my/pkg@abc123"
        return expected

    monkeypatch.setattr(
        "lambda_handlers.package_resolver.handler.resolve_package_manifest", fake_resolve
    )

    from lambda_handlers.package_resolver.handler import resolve_manifest

    assert resolve_manifest("quilt+s3://registry#package=my/pkg@abc123") == expected


def test_resolve_translation_map_delegates(monkeypatch) -> None:
    expected = PackageMap(entries={"logical/file.csv": [S3Location(bucket="bucket", key="key")]})

    def fake_resolve(quilt_uri: str) -> PackageMap:
        assert quilt_uri == "quilt+s3://registry#package=my/pkg@abc123"
        return expected

    monkeypatch.setattr(
        "lambda_handlers.package_resolver.handler.resolve_package_map", fake_resolve
    )

    from lambda_handlers.package_resolver.handler import resolve_translation_map

    assert resolve_translation_map("quilt+s3://registry#package=my/pkg@abc123") == expected


def test_check_membership_delegates(monkeypatch) -> None:
    def fake_checker(quilt_uri: str, bucket: str, key: str) -> bool:
        assert quilt_uri == "quilt+s3://registry#package=my/pkg@abc123"
        assert bucket == "bucket"
        assert key == "data/file.csv"
        return True

    monkeypatch.setattr(
        "lambda_handlers.package_resolver.handler.package_membership_checker", fake_checker
    )

    from lambda_handlers.package_resolver.handler import check_membership

    assert check_membership("quilt+s3://registry#package=my/pkg@abc123", "bucket", "data/file.csv")
