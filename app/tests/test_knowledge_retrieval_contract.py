from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, cast

import httpx
import pytest

from app.domain.agent.knowledge import (
    Citation,
    KnowledgeEvidence,
    KnowledgeQuery,
    KnowledgeRetrievalResult,
)
from app.infrastructure.external.knowledge_retrieval import (
    KnowledgeRetrievalAuthorizationError,
    KnowledgeRetrievalClient,
    KnowledgeRetrievalProtocolError,
)

INVESTMENT_ROOT = Path(
    os.environ.get(
        "INVESTMENT_APP_ROOT",
        str(Path(__file__).resolve().parents[3]),
    )
)
LOCK_PATH = INVESTMENT_ROOT / "contracts/knowledge-retrieval-provider-lock.json"


def _provider_dir() -> Path:
    configured = os.environ.get("KNOWLEDGE_RETRIEVAL_CONTRACT_DIR")
    if configured:
        return Path(configured)
    return INVESTMENT_ROOT.parent / "knowledge-app/contracts/retrieval/v1"


def _provider_example(name: str) -> dict[str, Any]:
    return json.loads((_provider_dir() / "examples" / name).read_text())


def _query() -> KnowledgeQuery:
    payload = _provider_example("request.json")
    payload.pop("contract_version")
    return KnowledgeQuery.model_validate(payload)


def test_provider_contract_lock_matches_authoritative_schemas() -> None:
    lock = json.loads(LOCK_PATH.read_text())
    provider_manifest = json.loads((_provider_dir() / "contract-manifest.json").read_text())

    assert lock["major"] == provider_manifest["major"] == 1
    for name, expected_digest in lock["schemas"].items():
        assert provider_manifest["files"][name] == expected_digest
        assert hashlib.sha256((_provider_dir() / name).read_bytes()).hexdigest() == (
            expected_digest
        )


def test_provider_examples_map_to_research_domain_without_provider_types() -> None:
    request = _provider_example("request.json")
    response = _provider_example("response.json")
    citation = _provider_example("citation.json")
    request.pop("contract_version")
    response.pop("contract_version")

    KnowledgeQuery.model_validate(request)
    result = KnowledgeRetrievalResult.model_validate(response)
    Citation.model_validate(citation)

    field_names = set(KnowledgeEvidence.model_fields)
    assert not any(name.startswith("ragflow_") for name in field_names)
    assert result.evidence[0].knowledge_document_version_id


class FakeTokenProvider:
    async def get_token(self) -> str:
        return "secret-service-token"


@pytest.mark.asyncio
async def test_client_sends_contract_v1_and_maps_response_to_domain() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("Authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_provider_example("response.json"))

    client = KnowledgeRetrievalClient(
        retrieval_url="http://knowledge/internal/v1/knowledge/retrievals",
        token_provider=cast(Any, FakeTokenProvider()),
        timeout_seconds=1,
        transport=httpx.MockTransport(handler),
    )

    result = await client.retrieve(_query())

    assert result.evidence
    assert captured["authorization"] == "Bearer secret-service-token"
    assert captured["body"]["contract_version"] == 1
    assert "ragflow" not in json.dumps(captured["body"])


@pytest.mark.asyncio
async def test_client_rejects_wrong_contract_version() -> None:
    response = _provider_example("response.json")
    response["contract_version"] = 2
    client = KnowledgeRetrievalClient(
        retrieval_url="http://knowledge/internal/v1/knowledge/retrievals",
        token_provider=cast(Any, FakeTokenProvider()),
        timeout_seconds=1,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response)),
    )

    with pytest.raises(KnowledgeRetrievalProtocolError):
        await client.retrieve(_query())


@pytest.mark.asyncio
async def test_client_does_not_expose_provider_error_body_on_denial() -> None:
    client = KnowledgeRetrievalClient(
        retrieval_url="http://knowledge/internal/v1/knowledge/retrievals",
        token_provider=cast(Any, FakeTokenProvider()),
        timeout_seconds=1,
        transport=httpx.MockTransport(
            lambda _: httpx.Response(
                403,
                json={"detail": "private provider dataset ragflow-dataset-secret"},
            )
        ),
    )

    with pytest.raises(KnowledgeRetrievalAuthorizationError) as error:
        await client.retrieve(_query())
    assert "ragflow" not in str(error.value)


def test_citation_projection_removes_source_uri_and_provider_metadata() -> None:
    response = _provider_example("response.json")
    response.pop("contract_version")
    evidence = KnowledgeRetrievalResult.model_validate(response).evidence[0]

    citation = Citation.from_evidence(evidence)
    raw = citation.model_dump(mode="json")

    assert "source_uri" not in raw
    assert "provider_metadata" not in raw
    assert citation.source_href.startswith("/api/citations/")
