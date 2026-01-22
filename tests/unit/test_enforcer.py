import time
from concurrent.futures import ThreadPoolExecutor

import jwt
import pytest

from raja.enforcer import (
    check_scopes,
    enforce,
    enforce_package_grant,
    enforce_translation_grant,
    enforce_with_routing,
    is_prefix_match,
)
from raja.exceptions import ScopeValidationError
from raja.models import AuthRequest, PackageAccessRequest, S3Location
from raja.package_map import PackageMap
from raja.token import (
    create_token,
    create_token_with_package_grant,
    create_token_with_package_map,
    decode_token,
)


def test_enforce_allows_matching_scope():
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is True
    assert decision.matched_scope == "Document:doc1:read"


def test_enforce_denies_missing_scope():
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="write")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "scope not granted"


def test_enforce_denies_invalid_token():
    secret = "secret"
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce("not-a-token", request, secret)
    assert decision.allowed is False
    assert decision.reason == "invalid token"


def test_enforce_denies_expired_token():
    """Test that expired tokens are denied with appropriate reason."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=-1, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "token expired"


def test_enforce_denies_wrong_signature():
    """Test that tokens with wrong signature are denied."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret="wrong-secret")
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "invalid token"


def test_check_scopes_validates_request():
    """Test that check_scopes properly validates the auth request."""
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    granted_scopes = ["Document:doc1:read"]
    result = check_scopes(request, granted_scopes)
    assert result is True


def test_check_scopes_denies_ungranted():
    """Test that check_scopes returns False for ungranted scopes."""
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="write")
    granted_scopes = ["Document:doc1:read"]
    result = check_scopes(request, granted_scopes)
    assert result is False


def test_check_scopes_handles_invalid_granted_scope():
    """Test that check_scopes raises error for invalid granted scope strings."""
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    granted_scopes = ["invalid-scope-format"]
    with pytest.raises(ScopeValidationError):
        check_scopes(request, granted_scopes)


def test_enforce_handles_scope_validation_error():
    """Test that enforce handles scope validation errors in check_scopes."""
    secret = "secret"
    token_str = create_token("alice", ["invalid-scope"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")

    # This will cause a ScopeValidationError when check_scopes tries to parse the granted scope
    decision = enforce(token_str, request, secret)

    # Should deny due to scope validation error
    assert decision.allowed is False
    assert "scope" in decision.reason.lower() or "internal error" in decision.reason.lower()


def test_enforce_logs_allowed_authorization():
    """Test that enforce properly logs successful authorization."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")

    decision = enforce(token_str, request, secret)

    assert decision.allowed is True
    assert decision.matched_scope == "Document:doc1:read"
    assert decision.reason == "scope matched"


def test_enforce_logs_denied_authorization():
    """Test that enforce properly logs denied authorization."""
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc2", action="read")

    decision = enforce(token_str, request, secret)

    assert decision.allowed is False
    assert decision.reason == "scope not granted"


def test_prefix_match_exact() -> None:
    assert is_prefix_match(
        "S3Object:bucket/key.txt:s3:GetObject",
        "S3Object:bucket/key.txt:s3:GetObject",
    )


def test_prefix_match_bucket_mismatch() -> None:
    assert not is_prefix_match(
        "S3Object:bucket/uploads/file.txt:s3:GetObject",
        "S3Object:other/uploads/file.txt:s3:GetObject",
    )


def test_prefix_match_key_prefix() -> None:
    assert is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/uploads/subdir/file.txt:s3:GetObject",
    )


def test_prefix_match_bucket_no_match() -> None:
    assert not is_prefix_match(
        "S3Bucket:bucket:s3:ListBucket",
        "S3Bucket:other:s3:ListBucket",
    )


def test_prefix_match_key_no_match() -> None:
    assert not is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/private/file.txt:s3:GetObject",
    )


def test_prefix_match_action_mismatch() -> None:
    assert not is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/uploads/file.txt:s3:PutObject",
    )


def test_prefix_match_bucket_only_scope() -> None:
    assert is_prefix_match(
        "S3Bucket:bucket:s3:ListBucket",
        "S3Bucket:bucket:s3:ListBucket",
    )


def test_prefix_match_head_object_implied_by_get() -> None:
    assert is_prefix_match(
        "S3Object:bucket/uploads/:s3:GetObject",
        "S3Object:bucket/uploads/file.txt:s3:HeadObject",
    )


def test_prefix_match_multipart_implied_by_put() -> None:
    assert is_prefix_match(
        "S3Object:bucket/uploads/:s3:PutObject",
        "S3Object:bucket/uploads/file.txt:s3:UploadPart",
    )


def test_prefix_match_bucket_prefix_rejected() -> None:
    assert not is_prefix_match(
        "S3Object:bucket-/:s3:GetObject",
        "S3Object:bucket-other/key.txt:s3:GetObject",
    )


def test_prefix_match_trailing_slash_ambiguity() -> None:
    assert is_prefix_match(
        "S3Object:bucket/prefix/:s3:GetObject",
        "S3Object:bucket/prefix/file.txt:s3:GetObject",
    )
    assert not is_prefix_match(
        "S3Object:bucket/prefix/:s3:GetObject",
        "S3Object:bucket/prefix-other/file.txt:s3:GetObject",
    )
    assert not is_prefix_match(
        "S3Object:bucket/prefix:s3:GetObject",
        "S3Object:bucket/prefix/file.txt:s3:GetObject",
    )


def test_prefix_match_resource_type_mismatch() -> None:
    assert not is_prefix_match(
        "S3Object:bucket/key.txt:s3:ListBucket",
        "S3Bucket:bucket:s3:ListBucket",
    )


def test_enforce_package_grant_allows_member() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice", quilt_uri=quilt_uri, mode="read", ttl=60, secret=secret
    )
    request = PackageAccessRequest(bucket="bucket", key="data/file.csv", action="s3:GetObject")

    def checker(uri: str, bucket: str, key: str) -> bool:
        return uri == quilt_uri and bucket == "bucket" and key == "data/file.csv"

    decision = enforce_package_grant(token_str, request, secret, checker)
    assert decision.allowed is True
    assert decision.matched_scope == quilt_uri


def test_enforce_package_grant_denies_non_member() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice", quilt_uri=quilt_uri, mode="read", ttl=60, secret=secret
    )
    request = PackageAccessRequest(bucket="bucket", key="other.csv", action="s3:GetObject")

    def checker(uri: str, bucket: str, key: str) -> bool:
        return False

    decision = enforce_package_grant(token_str, request, secret, checker)
    assert decision.allowed is False
    assert decision.reason == "object not in package"


def test_enforce_package_grant_denies_write_with_read_mode() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice", quilt_uri=quilt_uri, mode="read", ttl=60, secret=secret
    )
    request = PackageAccessRequest(bucket="bucket", key="data/file.csv", action="s3:PutObject")

    def checker(uri: str, bucket: str, key: str) -> bool:
        return True

    decision = enforce_package_grant(token_str, request, secret, checker)
    assert decision.allowed is False
    assert decision.reason == "action not permitted by token mode"


def test_enforce_package_grant_denies_on_checker_error() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice", quilt_uri=quilt_uri, mode="read", ttl=60, secret=secret
    )
    request = PackageAccessRequest(bucket="bucket", key="data/file.csv", action="s3:GetObject")

    def checker(uri: str, bucket: str, key: str) -> bool:
        raise RuntimeError("boom")

    decision = enforce_package_grant(token_str, request, secret, checker)
    assert decision.allowed is False
    assert decision.reason == "package membership check failed"


def test_enforce_translation_grant_allows_and_returns_targets() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=60,
        secret=secret,
    )
    request = PackageAccessRequest(
        bucket="logical-bucket", key="logical/file.csv", action="s3:GetObject"
    )

    def resolver(uri: str) -> PackageMap:
        assert uri == quilt_uri
        return PackageMap(
            entries={
                "logical/file.csv": [S3Location(bucket="physical-bucket", key="data/file.csv")]
            }
        )

    decision = enforce_translation_grant(token_str, request, secret, resolver)
    assert decision.allowed is True
    assert decision.matched_scope == quilt_uri
    assert decision.translated_targets == [
        S3Location(bucket="physical-bucket", key="data/file.csv")
    ]


def test_enforce_translation_grant_denies_bucket_mismatch() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=60,
        secret=secret,
    )
    request = PackageAccessRequest(
        bucket="other-bucket", key="logical/file.csv", action="s3:GetObject"
    )

    def resolver(uri: str) -> PackageMap:
        return PackageMap(entries={})

    decision = enforce_translation_grant(token_str, request, secret, resolver)
    assert decision.allowed is False
    assert decision.reason == "logical request not permitted by token"


def test_enforce_translation_grant_denies_unmapped_key() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=60,
        secret=secret,
    )
    request = PackageAccessRequest(
        bucket="logical-bucket", key="logical/file.csv", action="s3:GetObject"
    )

    def resolver(uri: str) -> PackageMap:
        return PackageMap(entries={})

    decision = enforce_translation_grant(token_str, request, secret, resolver)
    assert decision.allowed is False
    assert decision.reason == "logical key not mapped in package"


def test_enforce_translation_grant_denies_on_resolver_error() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=60,
        secret=secret,
    )
    request = PackageAccessRequest(
        bucket="logical-bucket", key="logical/file.csv", action="s3:GetObject"
    )

    def resolver(uri: str) -> PackageMap:
        raise RuntimeError("boom")

    decision = enforce_translation_grant(token_str, request, secret, resolver)
    assert decision.allowed is False
    assert decision.reason == "package map translation failed"


def test_enforce_with_routing_uses_scopes_token() -> None:
    secret = "secret"
    token_str = create_token("alice", ["Document:doc1:read"], ttl=60, secret=secret)
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce_with_routing(token_str, request, secret)
    assert decision.allowed is True


def test_enforce_with_routing_uses_package_grant() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice", quilt_uri=quilt_uri, mode="read", ttl=60, secret=secret
    )
    request = PackageAccessRequest(bucket="bucket", key="data/file.csv", action="s3:GetObject")

    def checker(uri: str, bucket: str, key: str) -> bool:
        return uri == quilt_uri and bucket == "bucket" and key == "data/file.csv"

    decision = enforce_with_routing(token_str, request, secret, membership_checker=checker)
    assert decision.allowed is True


def test_enforce_with_routing_uses_translation_grant() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_map(
        "alice",
        quilt_uri=quilt_uri,
        mode="read",
        logical_bucket="logical-bucket",
        logical_key="logical/file.csv",
        ttl=60,
        secret=secret,
    )
    request = PackageAccessRequest(
        bucket="logical-bucket", key="logical/file.csv", action="s3:GetObject"
    )

    def resolver(uri: str) -> PackageMap:
        return PackageMap(
            entries={
                "logical/file.csv": [S3Location(bucket="physical-bucket", key="data/file.csv")]
            }
        )

    decision = enforce_with_routing(token_str, request, secret, manifest_resolver=resolver)
    assert decision.allowed is True


def test_enforce_with_routing_rejects_mixed_token() -> None:
    token_str = create_token_with_package_grant(
        "alice",
        quilt_uri="quilt+s3://registry#package=my/pkg@abc123def456",
        mode="read",
        ttl=60,
        secret="secret",
    )
    mixed_payload = {
        **decode_token(token_str),
        "scopes": ["Document:doc1:read"],
    }
    mixed_token = jwt.encode(mixed_payload, "secret", algorithm="HS256")
    request = PackageAccessRequest(bucket="bucket", key="data/file.csv", action="s3:GetObject")
    decision = enforce_with_routing(mixed_token, request, "secret")
    assert decision.allowed is False
    assert decision.reason == "mixed token types are not supported"


def test_enforce_with_routing_requires_handlers() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice", quilt_uri=quilt_uri, mode="read", ttl=60, secret=secret
    )
    request = PackageAccessRequest(bucket="bucket", key="data/file.csv", action="s3:GetObject")
    decision = enforce_with_routing(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "membership checker is required"


def test_enforce_with_routing_rejects_invalid_request() -> None:
    secret = "secret"
    quilt_uri = "quilt+s3://registry#package=my/pkg@abc123def456"
    token_str = create_token_with_package_grant(
        "alice", quilt_uri=quilt_uri, mode="read", ttl=60, secret=secret
    )
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    decision = enforce_with_routing(token_str, request, secret)
    assert decision.allowed is False
    assert decision.reason == "invalid request for package token"


def test_check_scopes_rejects_missing_action() -> None:
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    with pytest.raises(ScopeValidationError):
        check_scopes(request, ["Document:doc1"])


@pytest.mark.slow
def test_check_scopes_large_token_performance() -> None:
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    granted_scopes = [f"Document:doc{i}:read" for i in range(2000)]
    granted_scopes.append("Document:doc1:read")
    start = time.perf_counter()
    assert check_scopes(request, granted_scopes) is True
    duration = time.perf_counter() - start
    assert duration < 0.5


@pytest.mark.slow
def test_check_scopes_concurrent_requests() -> None:
    request = AuthRequest(resource_type="Document", resource_id="doc1", action="read")
    granted_scopes = ["Document:doc1:read"]

    def _run() -> bool:
        return check_scopes(request, granted_scopes)

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: _run(), range(50)))

    assert all(results)
