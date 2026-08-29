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
    provider_manifest = json.loads(
        (_provider_dir() / "contract-manifest.json").read_text()
    )

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
    assert citation.source_href.startswith("/api/web/v1/citations/")


def test_citation_source_href_matches_investment_own_routes() -> None:
    """consumer 侧的 source_href 也必须能在本仓路由表里找到。

    O6 在本仓有两处：`domain/agent/knowledge.Citation`（检索契约投影）与
    `application/dto/pilot_runtime.BrowserCitation`。两者由
    `tasks/pilot_agent_graph.py` 写事件、`agent/pilot_service.py` 读回来串在
    一起，pattern 必须一致，否则 `model_validate` 直接抛。

    而 `application/dto/interaction.BrowserCitation` 与前端
    `contracts/interaction.ts` 早就用的是 `/api/web/v1/`——本仓自己内部就不一致。
    """
    import re
    import uuid as _uuid

    from fastapi.routing import APIRoute

    from app.application.dto.pilot_runtime import BrowserCitation
    from app.bootstrap.api import create_app
    from app.domain.agent.knowledge import Citation

    routes = [r.path for r in create_app().routes if isinstance(r, APIRoute)]
    concrete = [re.sub(r"\{[^}]+\}", str(_uuid.uuid4()), p) for p in routes]

    for model in (Citation, BrowserCitation):
        pattern = model.model_fields["source_href"].metadata[-1].pattern
        assert any(re.match(pattern, p) for p in concrete), (
            f"{model.__name__}.source_href 的 pattern {pattern!r} "
            f"匹配不到任何真实路由；含 citations 的路由有："
            f"{[p for p in routes if 'citations' in p]}"
        )

    # 两个类必须同形：它们经事件存储首尾相接
    assert (
        Citation.model_fields["source_href"].metadata[-1].pattern
        == BrowserCitation.model_fields["source_href"].metadata[-1].pattern
    )
