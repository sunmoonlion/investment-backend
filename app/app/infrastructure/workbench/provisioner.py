"""沙箱供给器的 HTTP 实现。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from app.application.workbench.provisioning import ProvisionerFailed


@dataclass(frozen=True)
class ProvisionerConfig:
    url: str
    token: str
    model_provider: str = ""
    model: str = ""
    provider_base_url: str = ""


class HttpProvisioner:
    def __init__(
        self,
        config: ProvisionerConfig,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config
        self.transport = transport

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.config.url,
            headers={"Authorization": f"Bearer {self.config.token}"},
            timeout=60,
            transport=self.transport,
        )

    async def upsert(self, user: str, spec: dict[str, Any]) -> dict[str, Any]:
        async with self._client() as c:
            try:
                r = await c.put(f"/sandboxes/{user}", json=spec)
            except httpx.HTTPError as exc:
                raise ProvisionerFailed("provisioner unreachable") from exc
        if r.status_code != 200:
            raise ProvisionerFailed(f"provisioner returned {r.status_code}")
        return r.json()

    async def status(self, user: str) -> dict[str, Any]:
        async with self._client() as c:
            try:
                r = await c.get(f"/sandboxes/{user}")
            except httpx.HTTPError as exc:
                raise ProvisionerFailed("provisioner unreachable") from exc
        if r.status_code != 200:
            raise ProvisionerFailed(f"provisioner returned {r.status_code}")
        return r.json()

    async def delete(self, user: str, purge: bool = False) -> dict[str, Any]:
        async with self._client() as c:
            try:
                r = await c.delete(
                    f"/sandboxes/{user}", params={"purge": "true"} if purge else None
                )
            except httpx.HTTPError as exc:
                raise ProvisionerFailed("provisioner unreachable") from exc
        if r.status_code != 200:
            raise ProvisionerFailed(f"provisioner returned {r.status_code}")
        return r.json()
