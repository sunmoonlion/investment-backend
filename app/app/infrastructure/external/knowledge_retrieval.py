from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.domain.agent.knowledge import (
    KnowledgePort,
    KnowledgeQuery,
    KnowledgeRetrievalResult,
)
from app.infrastructure.security import OidcProviderClient
from core.config import Settings, get_settings


class KnowledgeRetrievalError(RuntimeError):
    pass


class KnowledgeRetrievalNotConfiguredError(KnowledgeRetrievalError):
    pass


class KnowledgeRetrievalAuthorizationError(KnowledgeRetrievalError):
    pass


class KnowledgeRetrievalUnavailableError(KnowledgeRetrievalError):
    pass


class KnowledgeRetrievalProtocolError(KnowledgeRetrievalError):
    pass


class RetrievalServiceTokenProvider:
    """Short-lived in-memory token cache for only the retrieval relation."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        discovery_url = (
            settings.knowledge_retrieval_service_discovery_url
            or settings.casdoor_discovery_endpoint
        )
        service_endpoint = settings.casdoor_endpoint
        if discovery_url:
            parsed = urlsplit(discovery_url)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                service_endpoint = urlunsplit((parsed.scheme, parsed.netloc, "", "", ""))
        service_settings = settings.model_copy(
            update={
                "casdoor_endpoint": service_endpoint,
                "casdoor_application": settings.knowledge_retrieval_service_application,
                "casdoor_discovery_url": discovery_url,
                "casdoor_backchannel_endpoint": (
                    settings.knowledge_retrieval_service_backchannel_endpoint
                ),
                "casdoor_client_id": settings.knowledge_retrieval_service_client_id or "",
                "casdoor_client_secret": (
                    settings.knowledge_retrieval_service_client_secret or ""
                ),
                "casdoor_redirect_uri": "",
            }
        )
        service_profile = replace(
            settings.browser_profile("admin"),
            client_id=settings.knowledge_retrieval_service_client_id or "",
            client_secret=settings.knowledge_retrieval_service_client_secret or "",
            redirect_uri="",
            application=settings.knowledge_retrieval_service_application,
            policy_version="investment-knowledge-retrieval-v1",
            required_scopes=(settings.knowledge_retrieval_service_scope,),
        )
        self._oidc = OidcProviderClient(service_settings, service_profile)
        self._access_token: str | None = None
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get_token(self) -> str:
        if not self._settings.knowledge_retrieval_service_client_id or not (
            self._settings.knowledge_retrieval_service_client_secret
        ):
            raise KnowledgeRetrievalNotConfiguredError(
                "Knowledge retrieval service credentials are not configured"
            )
        now = time.time()
        if self._access_token and self._expires_at > now + 30:
            return self._access_token
        async with self._lock:
            now = time.time()
            if self._access_token and self._expires_at > now + 30:
                return self._access_token
            body = await self._oidc.exchange_client_credentials(
                scope=self._settings.knowledge_retrieval_service_scope
            )
            token = body.get("access_token") if isinstance(body, dict) else None
            expires_in = body.get("expires_in", 300) if isinstance(body, dict) else None
            if not isinstance(token, str) or not token:
                raise KnowledgeRetrievalProtocolError("service access token missing")
            if not isinstance(expires_in, int | float) or expires_in <= 0:
                raise KnowledgeRetrievalProtocolError("service access token expiry invalid")
            self._access_token = token
            self._expires_at = time.time() + float(expires_in)
            return token


class KnowledgeRetrievalClient(KnowledgePort):
    def __init__(
        self,
        *,
        retrieval_url: str | None,
        token_provider: RetrievalServiceTokenProvider | None,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._retrieval_url = retrieval_url
        self._token_provider = token_provider
        self._timeout_seconds = timeout_seconds
        self._transport = transport

    async def retrieve(self, query: KnowledgeQuery) -> KnowledgeRetrievalResult:
        if not self._retrieval_url or not self._token_provider:
            raise KnowledgeRetrievalNotConfiguredError(
                "Knowledge retrieval URL or service credentials are not configured"
            )
        token = await self._token_provider.get_token()
        payload = {
            "contract_version": 1,
            **query.model_dump(mode="json"),
        }
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                response = await client.post(
                    self._retrieval_url,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Accept": "application/json",
                    },
                )
        except httpx.TimeoutException as exc:
            raise KnowledgeRetrievalUnavailableError("Knowledge retrieval timed out") from exc
        except httpx.HTTPError as exc:
            raise KnowledgeRetrievalUnavailableError("Knowledge retrieval unavailable") from exc
        if response.status_code in {401, 403}:
            raise KnowledgeRetrievalAuthorizationError("Knowledge retrieval was denied")
        if response.status_code != 200:
            raise KnowledgeRetrievalUnavailableError(
                f"Knowledge retrieval failed with HTTP {response.status_code}"
            )
        try:
            body: Any = response.json()
            if not isinstance(body, dict) or body.get("contract_version") != 1:
                raise ValueError("unsupported Knowledge retrieval contract")
            domain_payload = dict(body)
            domain_payload.pop("contract_version")
            return KnowledgeRetrievalResult.model_validate(domain_payload)
        except (ValueError, TypeError) as exc:
            raise KnowledgeRetrievalProtocolError(
                "Knowledge retrieval response violates contract v1"
            ) from exc


@lru_cache(maxsize=1)
def get_knowledge_retrieval_client() -> KnowledgeRetrievalClient:
    settings = get_settings()
    return KnowledgeRetrievalClient(
        retrieval_url=settings.knowledge_retrieval_url,
        token_provider=(
            RetrievalServiceTokenProvider(settings)
            if settings.knowledge_retrieval_enabled
            else None
        ),
        timeout_seconds=settings.knowledge_retrieval_timeout_seconds,
    )
