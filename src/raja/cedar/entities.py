"""Cedar entity parsing utilities.

This module provides utilities for parsing Cedar entity identifiers
from their string representation.
"""

from __future__ import annotations

import re

# Regex pattern for matching Cedar entity format: Type::"id"
_ENTITY_RE = re.compile(r'^(?P<type>.+)::"(?P<id>[^"]+)"$')


def parse_entity(entity_str: str) -> tuple[str, str]:
    """Parse a Cedar entity string into its type and ID components.

    Cedar entities are formatted as `Type::"id"` where Type can be
    a simple name or a namespaced name like `Namespace::Type`.

    Args:
        entity_str: A Cedar entity string in the format `Type::"id"`

    Returns:
        A tuple of (type_name, entity_id) where type_name is the last
        component of the type (e.g., "User" from "App::User") and
        entity_id is the identifier.

    Raises:
        ValueError: If the entity string is not in the correct format

    Examples:
        >>> parse_entity('User::"alice"')
        ('User', 'alice')

        >>> parse_entity('App::User::"alice"')
        ('User', 'alice')

        >>> parse_entity('Document::"doc123"')
        ('Document', 'doc123')

        >>> parse_entity('Action::"read"')
        ('Action', 'read')

        >>> parse_entity('invalid')
        Traceback (most recent call last):
            ...
        ValueError: entity must be in the form Type::"id"
    """
    match = _ENTITY_RE.match(entity_str.strip())
    if not match:
        raise ValueError('entity must be in the form Type::"id"')
    raw_type = match.group("type")
    return raw_type.split("::")[-1], match.group("id")
