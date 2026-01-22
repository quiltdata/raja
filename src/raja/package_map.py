from __future__ import annotations

from pydantic import BaseModel, field_validator

from .models import S3Location


class PackageMap(BaseModel):
    entries: dict[str, list[S3Location]]

    @field_validator("entries")
    @classmethod
    def _entries_non_null(cls, value: dict[str, list[S3Location]]) -> dict[str, list[S3Location]]:
        return value or {}

    def translate(self, logical_key: str) -> list[S3Location]:
        if not logical_key or logical_key.strip() == "":
            raise ValueError("logical key must be non-empty")
        return self.entries.get(logical_key, [])


def parse_s3_path(value: str) -> tuple[str, str]:
    if not value or value.strip() == "":
        raise ValueError("logical s3 path must be non-empty")
    if not value.startswith("s3://"):
        raise ValueError("logical s3 path must start with s3://")
    path = value[len("s3://") :]
    if "/" not in path:
        raise ValueError("logical s3 path must include bucket and key")
    bucket, key = path.split("/", 1)
    if not bucket or not key:
        raise ValueError("logical s3 path must include bucket and key")
    return bucket, key
