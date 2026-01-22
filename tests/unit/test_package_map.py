from raja.models import S3Location
from raja.package_map import PackageMap


def test_package_map_translate_returns_targets() -> None:
    targets = [
        S3Location(bucket="physical-bucket", key="data/file.txt"),
        S3Location(bucket="archive-bucket", key="data/file.txt"),
    ]
    package_map = PackageMap(entries={"logical/file.txt": targets})

    resolved = package_map.translate("logical/file.txt")

    assert resolved == targets


def test_package_map_translate_unknown_key_returns_empty() -> None:
    package_map = PackageMap(entries={"logical/file.txt": []})

    resolved = package_map.translate("logical/unknown.txt")

    assert resolved == []
