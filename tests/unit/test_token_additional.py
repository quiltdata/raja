from __future__ import annotations

import time

import jwt
import pytest

from raja.exceptions import TokenValidationError
from raja.token import (
    create_taj_token,
    validate_package_map_token,
    validate_package_token,
    validate_taj_token,
)


def test_validate_package_token_requires_subject() -> None:
    token_str = jwt.encode(
        {"quilt_uri": "quilt+s3://registry#package=my/pkg@abc123def456", "mode": "read"},
        "secret",
        algorithm="HS256",
    )

    with pytest.raises(TokenValidationError, match="subject is required"):
        validate_package_token(token_str, "secret")


def test_validate_package_map_token_uses_logical_s3_path() -> None:
    token_str = jwt.encode(
        {
            "sub": "alice",
            "quilt_uri": "quilt+s3://registry#package=my/pkg@abc123def456",
            "mode": "read",
            "logical_s3_path": "s3://logical-bucket/logical/path.csv",
            "iat": int(time.time()),
            "exp": int(time.time()) + 60,
        },
        "secret",
        algorithm="HS256",
    )

    token = validate_package_map_token(token_str, "secret")
    assert token.logical_bucket == "logical-bucket"
    assert token.logical_key == "logical/path.csv"


def test_validate_package_map_token_rejects_mismatched_logical_bucket() -> None:
    token_str = jwt.encode(
        {
            "sub": "alice",
            "quilt_uri": "quilt+s3://registry#package=my/pkg@abc123def456",
            "mode": "read",
            "logical_bucket": "other-bucket",
            "logical_s3_path": "s3://logical-bucket/logical/path.csv",
        },
        "secret",
        algorithm="HS256",
    )

    with pytest.raises(TokenValidationError, match="logical_bucket does not match"):
        validate_package_map_token(token_str, "secret")


def test_validate_taj_token_returns_model() -> None:
    token_str = create_taj_token(
        subject="alice",
        grants=["s3:GetObject/registry/pkg@hash/"],
        manifest_hash="hash",
        package_name="author/pkg",
        registry="registry",
        ttl=60,
        secret="secret",
    )

    token = validate_taj_token(token_str, "secret")
    assert token.subject == "alice"
    assert token.package_name == "author/pkg"
    assert token.registry == "registry"


def test_validate_taj_token_requires_grants_list() -> None:
    token_str = jwt.encode(
        {
            "sub": "alice",
            "grants": "not-a-list",
            "manifest_hash": "hash",
            "package_name": "author/pkg",
            "registry": "registry",
        },
        "secret",
        algorithm="HS256",
    )

    with pytest.raises(TokenValidationError, match="grants must be a list"):
        validate_taj_token(token_str, "secret")
