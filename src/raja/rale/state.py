from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RunMode = Literal["auto", "manual"]


@dataclass(frozen=True)
class ResolvedConfig:
    server_url: str
    registry: str
    rajee_endpoint: str
    admin_key: str
    principal: str
    tf_dir: str
    rale_authorizer_url: str = ""
    rale_router_url: str = ""


@dataclass
class SessionState:
    config: ResolvedConfig
    tf_outputs: dict[str, Any] = field(default_factory=dict)
    package_name: str | None = None
    manifest_hash: str | None = None
    logical_path: str | None = None
    usl: str | None = None
    taj: str | None = None
    taj_claims: dict[str, Any] | None = None

    def ensure_usl(self) -> str:
        if not self.usl:
            raise RuntimeError("No USL available - run setup phase first.")
        return self.usl

    def ensure_taj(self) -> str:
        if not self.taj:
            raise RuntimeError("No TAJ available - run authorization phase first.")
        return self.taj
