from __future__ import annotations

from types import SimpleNamespace

from raja.manifest import (
    package_membership_checker,
    resolve_package_manifest,
    resolve_package_map,
)
from raja.models import S3Location


class _FakePackage:
    def __init__(self) -> None:
        self._entries = [
            ("logical/file.csv", SimpleNamespace(bucket="bucket-a", key="data/file.csv")),
            ("logical/other.csv", SimpleNamespace(bucket="bucket-b", key="data/other.csv")),
        ]

    def walk(self):
        return iter(self._entries)


class _FakeQuilt3:
    class Package:
        @staticmethod
        def browse(name: str, registry: str, top_hash: str) -> _FakePackage:
            assert name == "my/pkg"
            assert registry == "s3://registry"
            assert top_hash == "abc123def456"
            return _FakePackage()


def _patch_quilt3(monkeypatch) -> None:
    monkeypatch.setattr("raja.manifest._load_quilt3", lambda: _FakeQuilt3)


def test_resolve_package_manifest(monkeypatch) -> None:
    _patch_quilt3(monkeypatch)
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    locations = resolve_package_manifest(quilt_uri)
    assert locations == [
        S3Location(bucket="bucket-a", key="data/file.csv"),
        S3Location(bucket="bucket-b", key="data/other.csv"),
    ]


def test_resolve_package_map(monkeypatch) -> None:
    _patch_quilt3(monkeypatch)
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    package_map = resolve_package_map(quilt_uri)
    assert package_map.translate("logical/file.csv") == [
        S3Location(bucket="bucket-a", key="data/file.csv")
    ]
    assert package_map.translate("logical/other.csv") == [
        S3Location(bucket="bucket-b", key="data/other.csv")
    ]


def test_package_membership_checker(monkeypatch) -> None:
    _patch_quilt3(monkeypatch)
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    assert package_membership_checker(quilt_uri, "bucket-a", "data/file.csv") is True
    assert package_membership_checker(quilt_uri, "bucket-a", "missing.csv") is False
