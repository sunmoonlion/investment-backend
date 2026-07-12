from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.agent.models import UserInput
from app.domain.agent.security import SecurityContext


class CreateRunCommand(BaseModel):
    session_id: str
    owner_actor_id: str | None = None
    user_input: UserInput = UserInput()
    idempotency_key: str | None = None
    agent_profile_key: str | None = None
    security_context: SecurityContext = Field(default_factory=SecurityContext.single_tenant)


class ResumeRunCommand(BaseModel):
    run_id: str
    owner_actor_id: str | None = None
    resume_token: str
    user_input: UserInput
    idempotency_key: str | None = None
    security_context: SecurityContext = Field(default_factory=SecurityContext.single_tenant)


class CancelRunCommand(BaseModel):
    run_id: str
    reason: str | None = None
    idempotency_key: str | None = None
    security_context: SecurityContext = Field(default_factory=SecurityContext.single_tenant)
