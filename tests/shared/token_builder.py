"""Shared token builder utility for test code.

This module provides a fluent builder API for constructing JWT tokens
across all test layers (unit, integration, admin GUI).

Consolidates token building logic previously duplicated in:
- tests/integration/test_failure_modes.py
- src/raja/server/routers/failure_tests.py
- tests/local/generate_test_token.py
"""

from __future__ import annotations

import time
from typing import Any

import jwt


class TokenBuilder:
    """Fluent builder for JWT tokens in tests.

    Usage:
        token = (
            TokenBuilder(secret="my-secret", issuer="https://test.local", audience="raja-s3")
            .with_subject("User::alice")
            .with_scopes(["S3Object:bucket/key:s3:GetObject"])
            .with_ttl(3600)
            .build()
        )

    For expired tokens:
        token = builder.with_expiration_in_past().build()

    For malformed tokens:
        token = builder.with_custom_claim("bad_claim", "value").build()
    """

    def __init__(
        self,
        *,
        secret: str,
        issuer: str,
        audience: str | list[str],
    ):
        """Initialize token builder with required parameters.

        Args:
            secret: JWT signing secret
            issuer: Token issuer (iss claim)
            audience: Token audience (aud claim) - string or list
        """
        self._secret = secret
        self._issuer = issuer
        self._audience = audience
        self._subject: str | None = None
        self._scopes: list[str] | None = None
        self._grants: list[str] | None = None
        self._issued_at: int | None = None
        self._expires_at: int | None = None
        self._ttl: int = 3600  # Default 1 hour
        self._custom_claims: dict[str, Any] = {}
        self._custom_headers: dict[str, str] = {}
        self._include_scopes = True
        self._algorithm = "HS256"

    def with_subject(self, subject: str) -> TokenBuilder:
        """Set the subject (sub claim).

        Args:
            subject: Subject identifier (e.g., "User::alice")

        Returns:
            Self for method chaining
        """
        self._subject = subject
        return self

    def with_scopes(self, scopes: list[str]) -> TokenBuilder:
        """Set the scopes claim.

        Args:
            scopes: List of scope strings

        Returns:
            Self for method chaining
        """
        self._scopes = scopes
        self._include_scopes = True
        return self

    def with_grants(self, grants: list[str]) -> TokenBuilder:
        """Set the grants claim (alternative to scopes).

        Args:
            grants: List of grant strings

        Returns:
            Self for method chaining
        """
        self._grants = grants
        return self

    def without_scopes(self) -> TokenBuilder:
        """Omit scopes claim entirely (for testing missing scopes).

        Returns:
            Self for method chaining
        """
        self._include_scopes = False
        return self

    def with_empty_scopes(self) -> TokenBuilder:
        """Set scopes to empty list (for testing empty scopes).

        Returns:
            Self for method chaining
        """
        self._scopes = []
        self._include_scopes = True
        return self

    def with_ttl(self, ttl_seconds: int) -> TokenBuilder:
        """Set time-to-live in seconds.

        Args:
            ttl_seconds: Seconds until expiration

        Returns:
            Self for method chaining
        """
        self._ttl = ttl_seconds
        return self

    def with_issued_at(self, issued_at: int) -> TokenBuilder:
        """Set explicit issued_at timestamp.

        Args:
            issued_at: Unix timestamp for iat claim

        Returns:
            Self for method chaining
        """
        self._issued_at = issued_at
        return self

    def with_expires_at(self, expires_at: int) -> TokenBuilder:
        """Set explicit expiration timestamp.

        Args:
            expires_at: Unix timestamp for exp claim

        Returns:
            Self for method chaining
        """
        self._expires_at = expires_at
        return self

    def with_expiration_in_past(self, seconds_ago: int = 60) -> TokenBuilder:
        """Set expiration in the past (for testing expired tokens).

        Args:
            seconds_ago: How many seconds ago the token expired

        Returns:
            Self for method chaining
        """
        now = int(time.time())
        self._issued_at = now - 3600  # Issued 1 hour ago
        self._expires_at = now - seconds_ago  # Expired N seconds ago
        return self

    def with_expiration_offset(self, offset_seconds: int) -> TokenBuilder:
        """Set expiration as offset from now.

        Args:
            offset_seconds: Seconds from now (negative for past)

        Returns:
            Self for method chaining
        """
        now = int(time.time())
        self._issued_at = now
        self._expires_at = now + offset_seconds
        return self

    def with_custom_claim(self, key: str, value: Any) -> TokenBuilder:
        """Add custom claim to payload.

        Args:
            key: Claim name
            value: Claim value

        Returns:
            Self for method chaining
        """
        self._custom_claims[key] = value
        return self

    def with_custom_header(self, key: str, value: str) -> TokenBuilder:
        """Add custom header to JWT.

        Args:
            key: Header name
            value: Header value

        Returns:
            Self for method chaining
        """
        self._custom_headers[key] = value
        return self

    def with_algorithm(self, algorithm: str) -> TokenBuilder:
        """Set signing algorithm.

        Args:
            algorithm: JWT algorithm (e.g., "HS256", "RS256")

        Returns:
            Self for method chaining
        """
        self._algorithm = algorithm
        return self

    def build(self) -> str:
        """Build and sign the JWT token.

        Returns:
            Encoded JWT string
        """
        # Calculate timestamps
        now = self._issued_at if self._issued_at is not None else int(time.time())
        exp = self._expires_at if self._expires_at is not None else now + self._ttl

        # Build payload
        payload: dict[str, Any] = {
            "iss": self._issuer,
            "aud": self._audience,
            "iat": now,
            "exp": exp,
        }

        # Add optional claims
        if self._subject is not None:
            payload["sub"] = self._subject

        if self._include_scopes and self._scopes is not None:
            payload["scopes"] = self._scopes

        if self._grants is not None:
            payload["grants"] = self._grants

        # Add custom claims
        payload.update(self._custom_claims)

        # Build headers
        headers = dict(self._custom_headers) if self._custom_headers else None

        # Encode and return
        return jwt.encode(payload, self._secret, algorithm=self._algorithm, headers=headers)


# Convenience function for simple token generation
def build_token(
    *,
    secret: str,
    issuer: str,
    audience: str | list[str],
    subject: str | None = None,
    scopes: list[str] | None = None,
    ttl_seconds: int = 3600,
) -> str:
    """Build a simple JWT token without using the fluent API.

    This is a convenience function for cases where the builder pattern
    would be overkill.

    Args:
        secret: JWT signing secret
        issuer: Token issuer
        audience: Token audience
        subject: Optional subject
        scopes: Optional scopes list
        ttl_seconds: Time to live in seconds

    Returns:
        Encoded JWT string
    """
    builder = TokenBuilder(secret=secret, issuer=issuer, audience=audience)

    if subject is not None:
        builder.with_subject(subject)

    if scopes is not None:
        builder.with_scopes(scopes)

    builder.with_ttl(ttl_seconds)

    return builder.build()
