from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    TypeAdapter,
    field_validator,
    model_validator,
)


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DelegatedUser(ContractModel):
    actor_id: UUID
    authenticated_at: datetime
    policy_version: str = Field(min_length=1, max_length=64)
    roles: tuple[str, ...] = Field(default=(), max_length=128)
    scopes: tuple[str, ...] = Field(default=(), max_length=128)


class PilotInput(ContractModel):
    text: str = Field(min_length=1, max_length=20000)


class PilotCreateRun(ContractModel):
    contract_version: Literal[1] = 1
    idempotency_key: UUID
    operation_id: str = Field(min_length=1, max_length=128)
    delegated_user: DelegatedUser
    title: str | None = Field(default=None, min_length=1, max_length=512)
    input: PilotInput


class PilotResumeCommand(ContractModel):
    contract_version: Literal[1] = 1
    kind: Literal["resume"]
    idempotency_key: UUID
    operation_id: str = Field(min_length=1, max_length=128)
    delegated_user: DelegatedUser
    action_id: UUID
    value: str = Field(max_length=4000)


class PilotCancelCommand(ContractModel):
    contract_version: Literal[1] = 1
    kind: Literal["cancel"]
    idempotency_key: UUID
    operation_id: str = Field(min_length=1, max_length=128)
    delegated_user: DelegatedUser
    reason: str | None = Field(default=None, max_length=1000)


PilotRunCommand = Annotated[
    PilotResumeCommand | PilotCancelCommand,
    Field(discriminator="kind"),
]
PILOT_COMMAND_ADAPTER = TypeAdapter(PilotRunCommand)


class BrowserCitation(ContractModel):
    contract_version: Literal[1] = 1
    evidence_id: UUID
    knowledge_document_id: UUID
    knowledge_document_version_id: UUID
    chunk_id: UUID
    title: str | None = Field(max_length=4096)
    quote: str = Field(min_length=1, max_length=1000)
    source_document_id: UUID
    source_document_version_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_href: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^/api/web/v1/citations/[0-9a-fA-F-]{36}/source$",
    )

    @model_validator(mode="after")
    def source_matches_evidence(self) -> BrowserCitation:
        if self.source_href.lower() != (
            f"/api/web/v1/citations/{self.evidence_id}/source".lower()
        ):
            raise ValueError("source_href must identify evidence_id")
        return self


class RequiredAction(ContractModel):
    action_id: UUID
    kind: Literal["confirmation", "input"]
    prompt: str = Field(min_length=1, max_length=2000)


RunStatus = Literal[
    "queued",
    "running",
    "waiting_for_input",
    "succeeded",
    "failed",
    "cancelled",
]


class PilotRunSnapshot(ContractModel):
    contract_version: Literal[1] = 1
    run_id: UUID
    title: str = Field(min_length=1, max_length=512)
    status: RunStatus
    summary: str | None = Field(default=None, max_length=20000)
    last_sequence_no: int = Field(ge=0)
    last_event_id: UUID | None
    citations: tuple[BrowserCitation, ...] = Field(max_length=50)
    required_action: RequiredAction | None
    updated_at: datetime

    @field_validator("updated_at")
    @classmethod
    def timestamp_has_offset(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("updated_at must contain a UTC offset")
        return value


class SourceResolution(ContractModel):
    location: str = Field(
        min_length=1,
        max_length=512,
        pattern=r"^/api/citation-sources/[0-9a-fA-F-]{36}$",
    )
