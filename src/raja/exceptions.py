"""Custom exception classes for RAJA authorization system.

This module defines specific exception types to improve error handling and debugging
throughout the codebase. Using specific exceptions instead of bare Exception makes
it easier to:
- Catch and handle specific error conditions
- Debug issues with clear error messages
- Provide better error context to users
"""

from __future__ import annotations


class RajaError(Exception):
    """Base exception class for all RAJA errors."""

    pass


class TokenError(RajaError):
    """Base exception class for token-related errors."""

    pass


class TokenValidationError(TokenError):
    """Raised when a token fails validation (signature, structure, claims)."""

    pass


class TokenExpiredError(TokenError):
    """Raised when a token has expired."""

    pass


class TokenInvalidError(TokenError):
    """Raised when a token is malformed or has invalid structure."""

    pass


class ScopeError(RajaError):
    """Base exception class for scope-related errors."""

    pass


class ScopeValidationError(ScopeError):
    """Raised when a scope string fails validation or parsing."""

    pass


class ScopeParseError(ScopeError):
    """Raised when a scope string cannot be parsed."""

    pass


class AuthorizationError(RajaError):
    """Base exception class for authorization-related errors."""

    pass


class InsufficientScopesError(AuthorizationError):
    """Raised when a principal lacks required scopes for an action."""

    pass
