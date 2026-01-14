"""Structured logging configuration for RAJA server.

This module provides structured JSON logging for CloudWatch compatibility.
Logs are formatted with ISO timestamps and include contextual information.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Any

import structlog


def configure_logging() -> None:
    """Configure structured logging for Lambda/CloudWatch environment.

    Sets up:
    - JSON output format for CloudWatch
    - ISO timestamp format
    - Log level filtering (INFO by default, configurable via LOG_LEVEL env var)
    - Automatic context propagation
    - Exception formatting

    Call this once at module initialization.
    """
    log_level = os.environ.get("LOG_LEVEL", "INFO").upper()

    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, log_level, logging.INFO),
    )

    # Configure structlog
    structlog.configure(
        processors=[
            # Add log level to event dict
            structlog.stdlib.add_log_level,
            # Add timestamp in ISO format
            structlog.processors.TimeStamper(fmt="iso"),
            # Add exception info if present
            structlog.processors.format_exc_info,
            # Render to JSON for CloudWatch
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str | None = None) -> Any:
    """Get a structured logger instance.

    Args:
        name: Logger name (typically __name__ from calling module)

    Returns:
        Structured logger with JSON output

    Example:
        logger = get_logger(__name__)
        logger.info("user_login", username="alice", ip_address="1.2.3.4")
    """
    return structlog.get_logger(name)


# Convenience function for masking sensitive data
def mask_token(token: str, visible_chars: int = 8) -> str:
    """Mask a token for safe logging.

    Args:
        token: Token string to mask
        visible_chars: Number of characters to show at start/end

    Returns:
        Masked token string (e.g., "eyJhbGci...xyz123ab")
    """
    if len(token) <= visible_chars * 2:
        return "***"
    return f"{token[:visible_chars]}...{token[-visible_chars:]}"
