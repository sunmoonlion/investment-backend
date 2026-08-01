from __future__ import annotations

import uuid
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator


class KnowledgeFilters(BaseModel):
    source_document_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)
    source_document_version_ids: list[uuid.UUID] = Field(default_factory=list, max_length=50)

    model_config = ConfigDict(extra="forbid")

    @field_validator("source_document_ids", "source_document_version_ids")
    @classmethod
    def require_unique_ids(cls, value: list[uuid.UUID]) -> list[uuid.UUID]:
        if len(value) != len(set(value)):
            raise ValueError("filter IDs must be unique")
        return value


class RetrievalSecurityContext(BaseModel):
    tenant_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    actor_id: uuid.UUID
    actor_type: Literal["human", "service"]
    policy_version: str = Field(min_length=1, max_length=120)
    delegated_run_id: uuid.UUID | None = None

    model_config = ConfigDict(extra="forbid")


class KnowledgeQuery(BaseModel):
    request_id: uuid.UUID
    query: str = Field(min_length=1, max_length=8192)
    dataset_keys: list[str] = Field(min_length=1, max_length=8)
    filters: KnowledgeFilters = Field(default_factory=KnowledgeFilters)
    top_k: int = Field(default=5, ge=1, le=50)
    token_budget: int = Field(default=4000, ge=1, le=32000)
    security_context: RetrievalSecurityContext

    model_config = ConfigDict(extra="forbid")

    @field_validator("query")
    @classmethod
    def reject_blank_query(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("query must not be blank")
        return value

    @field_validator("dataset_keys")
    @classmethod
    def validate_dataset_keys(cls, value: list[str]) -> list[str]:
        import re

        if len(value) != len(set(value)):
            raise ValueError("dataset_keys must be unique")
        pattern = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
        if any(len(item) > 120 or not pattern.fullmatch(item) for item in value):
            raise ValueError("dataset key is invalid")
        return value


class EvidenceProviderMetadata(BaseModel):
    provider: str = Field(min_length=1, max_length=40)
    term_similarity: float | None = Field(default=None, ge=0, le=1)
    vector_similarity: float | None = Field(default=None, ge=0, le=1)

    model_config = ConfigDict(extra="forbid")


class KnowledgeEvidence(BaseModel):
    evidence_id: uuid.UUID
    knowledge_document_id: uuid.UUID
    knowledge_document_version_id: uuid.UUID
    chunk_id: uuid.UUID
    content: str = Field(min_length=1, max_length=200000)
    score: float = Field(ge=0, le=1)
    rank: int = Field(ge=1, le=50)
    title: str | None = Field(default=None, max_length=4096)
    source_uri: str | None = Field(default=None, max_length=8192)
    source_app: Literal["info-app"]
    source_document_id: uuid.UUID
    source_document_version_id: uuid.UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    token_estimate: int = Field(ge=1)
    truncated: bool
    access_scope: list[str] = Field(min_length=1, max_length=20)
    provider_metadata: EvidenceProviderMetadata

    model_config = ConfigDict(extra="forbid")


class KnowledgeRetrievalResult(BaseModel):
    retrieval_id: uuid.UUID
    request_id: uuid.UUID
    evidence: list[KnowledgeEvidence] = Field(max_length=50)
    total_candidates: int = Field(ge=0)
    truncated: bool

    model_config = ConfigDict(extra="forbid")


class Citation(BaseModel):
    contract_version: Literal[1] = 1
    evidence_id: uuid.UUID
    knowledge_document_id: uuid.UUID
    knowledge_document_version_id: uuid.UUID
    chunk_id: uuid.UUID
    title: str | None = Field(default=None, max_length=4096)
    quote: str = Field(min_length=1, max_length=1000)
    source_document_id: uuid.UUID
    source_document_version_id: uuid.UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    source_href: str = Field(
        max_length=128,
        pattern=r"^/api/citations/[0-9a-fA-F-]{36}/source$",
    )

    model_config = ConfigDict(extra="forbid")

    @classmethod
    def from_evidence(cls, evidence: KnowledgeEvidence) -> Citation:
        return cls(
            evidence_id=evidence.evidence_id,
            knowledge_document_id=evidence.knowledge_document_id,
            knowledge_document_version_id=evidence.knowledge_document_version_id,
            chunk_id=evidence.chunk_id,
            title=evidence.title,
            quote=evidence.content[:1000],
            source_document_id=evidence.source_document_id,
            source_document_version_id=evidence.source_document_version_id,
            content_hash=evidence.content_hash,
            source_href=f"/api/citations/{evidence.evidence_id}/source",
        )


class KnowledgePort(Protocol):
    async def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        ...
