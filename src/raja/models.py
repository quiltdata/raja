from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class ResourceValidatorMixin(BaseModel):
    """Mixin class providing shared validation logic for resource-based models.

    This mixin provides two validators:
    1. _no_empty: Ensures resource_type, resource_id, and action are non-empty
    2. _no_colon: Ensures resource_type and resource_id do not contain colons

    The colon restriction is necessary because scopes use colons as delimiters
    (ResourceType:ResourceId:Action), so colons in these fields would break parsing.
    """

    @field_validator("resource_type", "resource_id", "action", mode="before", check_fields=False)
    @classmethod
    def _no_empty(cls, value: str) -> str:
        """Validate that resource fields are non-empty strings.

        Args:
            value: The field value to validate

        Returns:
            The validated value

        Raises:
            ValueError: If value is empty or whitespace-only
        """
        if not value or value.strip() == "":
            raise ValueError("value must be non-empty")
        return value

    @field_validator("resource_type", "resource_id", mode="before", check_fields=False)
    @classmethod
    def _no_colon(cls, value: str) -> str:
        """Validate that resource_type and resource_id do not contain colons.

        Colons are reserved as scope delimiters (ResourceType:ResourceId:Action).

        Args:
            value: The field value to validate

        Returns:
            The validated value

        Raises:
            ValueError: If value contains a colon character
        """
        if ":" in value:
            raise ValueError("resource_type and resource_id must not contain ':'")
        return value


class Scope(ResourceValidatorMixin):
    resource_type: str
    resource_id: str
    action: str


class AuthRequest(ResourceValidatorMixin):
    resource_type: str
    resource_id: str
    action: str
    context: dict[str, Any] | None = None


class Decision(BaseModel):
    allowed: bool
    reason: str
    matched_scope: str | None = None


class Token(BaseModel):
    subject: str
    scopes: list[str]
    issued_at: int
    expires_at: int

    @field_validator("subject")
    @classmethod
    def _subject_non_empty(cls, value: str) -> str:
        if not value or value.strip() == "":
            raise ValueError("subject must be non-empty")
        return value


class CedarPolicy(BaseModel):
    effect: Literal["permit", "forbid"]
    principal: str
    action: str
    resource: str
    conditions: list[str] = Field(default_factory=list)

    @field_validator("principal", "action", "resource")
    @classmethod
    def _policy_parts_non_empty(cls, value: str) -> str:
        if not value or value.strip() == "":
            raise ValueError("value must be non-empty")
        return value
