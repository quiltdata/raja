from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


class Scope(BaseModel):
    resource_type: str
    resource_id: str
    action: str

    @field_validator("resource_type", "resource_id", "action")
    @classmethod
    def _no_empty(cls, value: str) -> str:
        if not value or value.strip() == "":
            raise ValueError("value must be non-empty")
        return value

    @field_validator("resource_type", "resource_id")
    @classmethod
    def _no_colon_in_resource(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("resource_type and resource_id must not contain ':'")
        return value


class AuthRequest(BaseModel):
    resource_type: str
    resource_id: str
    action: str
    context: dict[str, Any] | None = None

    @field_validator("resource_type", "resource_id", "action")
    @classmethod
    def _no_empty(cls, value: str) -> str:
        if not value or value.strip() == "":
            raise ValueError("value must be non-empty")
        return value

    @field_validator("resource_type", "resource_id")
    @classmethod
    def _no_colon_in_resource(cls, value: str) -> str:
        if ":" in value:
            raise ValueError("resource_type and resource_id must not contain ':'")
        return value


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
