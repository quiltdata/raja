import pytest

from raja.enforcer import enforce_translation_grant
from raja.models import PackageAccessRequest, S3Location
from raja.package_map import PackageMap
from raja.token import create_token_with_package_map

from .helpers import fetch_jwks_secret


@pytest.mark.integration
def test_translation_grant_allows_with_control_plane_secret() -> None:
    secret = fetch_jwks_secret()
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "test-user",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=300,
        secret=secret,
    )
    request = PackageAccessRequest(
        bucket="logical-bucket", key="logical/file.csv", action="s3:GetObject"
    )

    def resolver(uri: str) -> PackageMap:
        assert uri == quilt_uri
        return PackageMap(
            entries={
                "logical/file.csv": [
                    S3Location(bucket="physical-bucket", key="data/file.csv")
                ]
            }
        )

    decision = enforce_translation_grant(token_str, request, secret, resolver)
    assert decision.allowed is True
    assert decision.translated_targets == [
        S3Location(bucket="physical-bucket", key="data/file.csv")
    ]
