from __future__ import annotations

from pydantic import BaseModel

from app.domain.agent.models import UserInput


class CreateRunCommand(BaseModel):
    session_id: str
    user_input: UserInput = UserInput()
    idempotency_key: str | None = None
    agent_profile_key: str | None = None


class ResumeRunCommand(BaseModel):
    run_id: str
    resume_token: str
    user_input: UserInput
    idempotency_key: str | None = None


class CancelRunCommand(BaseModel):
    run_id: str
    reason: str | None = None
    idempotency_key: str | None = None
