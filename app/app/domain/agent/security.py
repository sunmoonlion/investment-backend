from __future__ import annotations

from pydantic import BaseModel, Field


class SecurityContext(BaseModel):
    tenant_id: str = "single-tenant"
    actor_id: str = "system"
    roles: list[str] = Field(default_factory=lambda: ["agent_user"])
    permissions: list[str] = Field(default_factory=list)
    schema_version: int = 1

    @classmethod
    def single_tenant(cls) -> SecurityContext:
        return cls()
