"""沙箱按需拉起（0003 D9）：用户的 key（设置页登记）→ 会合点身份 → 供给器建 pod → 登记为该用户的沙箱。

一用户一个沙箱。厂商 key 只在这里解密、只在内存里经过、经内网送到供给器，不落日志不进事件。
会合点令牌由工作台签发（每用户一对），密文入库，经管理通道登记到会合点；代理令牌只在签发时回给用户一次。
"""

from __future__ import annotations

import json
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.domain.workbench.errors import WorkbenchError
from app.infrastructure.workbench.repository import WorkbenchRepository

log = logging.getLogger(__name__)


class ProvisioningUnavailable(WorkbenchError):
    code = "provisioning_unavailable"
    http_status = 503


class NoCredential(WorkbenchError):
    code = "no_active_credential"
    http_status = 409


class ProvisionerFailed(WorkbenchError):
    code = "provisioner_failed"
    http_status = 502


class RelayAdminFailed(WorkbenchError):
    code = "relay_admin_failed"
    http_status = 502


class Cipher(Protocol):
    def encrypt(self, data: bytes) -> bytes: ...
    def decrypt(self, token: bytes) -> bytes: ...


class RelayAdmin(Protocol):
    async def set_tokens(self, user: str, agent: str, sandbox: str) -> None: ...
    async def revoke(self, user: str) -> None: ...


class WsRelayAdmin:
    """会合点管理通道：一条短连接，hello 后发一条命令。"""

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/") + "/admin"
        self.token = token

    async def _send(self, message: dict[str, Any]) -> dict[str, Any]:
        from websockets.asyncio.client import connect

        try:
            async with connect(self.url, open_timeout=10) as ws:
                await ws.send(
                    json.dumps({"type": "hello", "role": "admin", "token": self.token})
                )
                welcome = json.loads(await ws.recv())
                if welcome.get("type") != "welcome":
                    raise RelayAdminFailed("relay admin auth failed")
                await ws.send(json.dumps(message))
                reply = json.loads(await ws.recv())
        except (OSError, ValueError) as exc:
            raise RelayAdminFailed("relay admin channel unreachable") from exc
        except Exception as exc:  # websockets 的异常族
            if isinstance(exc, WorkbenchError):
                raise
            raise RelayAdminFailed("relay admin channel failed") from exc
        if reply.get("type") != "ok":
            raise RelayAdminFailed(f"relay refused: {reply.get('reason', 'unknown')}")
        return reply

    async def set_tokens(self, user: str, agent: str, sandbox: str) -> None:
        await self._send(
            {"type": "set_tokens", "user": user, "agent": agent, "sandbox": sandbox}
        )

    async def revoke(self, user: str) -> None:
        await self._send({"type": "revoke", "user": user})


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


def relay_user_for(owner_actor_id: str) -> str:
    """稳定、可读、合 DNS 标签：u-<actor id 前 12 位>。"""
    return "u-" + re.sub(r"[^0-9a-f]", "", owner_actor_id.lower())[:12]


class SandboxProvisioning:
    def __init__(
        self,
        repo: WorkbenchRepository,
        *,
        cipher: Cipher,
        provisioner: HttpProvisioner,
        relay_admin: RelayAdmin,
        relay_public_url: str,
        codex_version: str = "0.155.1",
    ) -> None:
        self.repo = repo
        self.cipher = cipher
        self.provisioner = provisioner
        self.relay_admin = relay_admin
        self.relay_public_url = relay_public_url
        self.codex_version = codex_version

    async def ensure_relay_identity(
        self, owner: str
    ) -> tuple[dict[str, Any], str | None]:
        """返回 (身份行, 新签发的代理令牌或 None)。令牌只在首次签发时明文返回。"""
        row = await self.repo.get_relay_identity(owner)
        fresh: str | None = None
        if row is None:
            agent = secrets.token_urlsafe(32)
            sandbox = secrets.token_urlsafe(32)
            async with self.repo.transaction():
                await self.repo.put_relay_identity(
                    owner,
                    relay_user=relay_user_for(owner),
                    agent_token_ciphertext=self.cipher.encrypt(agent.encode()).decode(),
                    sandbox_token_ciphertext=self.cipher.encrypt(
                        sandbox.encode()
                    ).decode(),
                )
            row = await self.repo.get_relay_identity(owner)
            fresh = agent
        assert row is not None
        agent_token = self.cipher.decrypt(
            row["agent_token_ciphertext"].encode()
        ).decode()
        sandbox_token = self.cipher.decrypt(
            row["sandbox_token_ciphertext"].encode()
        ).decode()
        await self.relay_admin.set_tokens(row["relay_user"], agent_token, sandbox_token)
        async with self.repo.transaction():
            await self.repo.mark_relay_registered(owner)
        row["_sandbox_token"] = sandbox_token
        return row, fresh

    async def provision(self, owner: str) -> dict[str, Any]:
        credential = await self.repo.active_credential(owner)
        if credential is None:
            raise NoCredential("register a model key in settings first")
        identity, fresh_agent_token = await self.ensure_relay_identity(owner)
        model_key = self.cipher.decrypt(credential["ciphertext"].encode()).decode()
        spec = {
            "model_provider": self.provisioner.config.model_provider,
            "model": self.provisioner.config.model,
            "provider_base_url": self.provisioner.config.provider_base_url,
            "model_key": model_key,
            "relay_token": identity["_sandbox_token"],
            "relay_user": identity["relay_user"],
        }
        result = await self.provisioner.upsert(identity["relay_user"], spec)
        del model_key, spec
        async with self.repo.transaction():
            sb_id = await self.repo.upsert_provisioned_sandbox(
                owner_actor_id=owner,
                app_server_url=result["app_server_url"],
                token_ref="inline:" + result["app_server_token"],
                codex_version=self.codex_version,
                relay_user=identity["relay_user"],
                status=result.get("status", "starting"),
            )
        out = {
            "sandbox_id": sb_id,
            "app_server_url": result["app_server_url"],
            "status": result.get("status"),
            "ready": bool(result.get("ready")),
            "relay": {
                "url": self.relay_public_url,
                "user": identity["relay_user"],
                "agent_token": fresh_agent_token,
            },
            "credential_hint": credential["hint"],
        }
        log.info(
            "sandbox provisioned owner=%s relay_user=%s status=%s",
            owner,
            identity["relay_user"],
            out["status"],
        )
        return out

    async def status(self, owner: str) -> dict[str, Any]:
        identity = await self.repo.get_relay_identity(owner)
        if identity is None:
            return {"status": "absent", "ready": False}
        result = await self.provisioner.status(identity["relay_user"])
        sandboxes = [
            s
            for s in await self.repo.list_sandboxes(owner_actor_id=owner)
            if s.get("provisioned")
        ]
        if (
            sandboxes
            and result.get("status")
            and sandboxes[0]["status"] != result["status"]
        ):
            async with self.repo.transaction():
                await self.repo.set_sandbox_status(
                    str(sandboxes[0]["id"]), result["status"]
                )
        return {
            **result,
            "sandbox_id": str(sandboxes[0]["id"]) if sandboxes else None,
            "relay_user": identity["relay_user"],
        }

    async def deprovision(self, owner: str, purge: bool = False) -> dict[str, Any]:
        identity = await self.repo.get_relay_identity(owner)
        if identity is None:
            return {"status": "absent"}
        result = await self.provisioner.delete(identity["relay_user"], purge=purge)
        for s in await self.repo.list_sandboxes(owner_actor_id=owner):
            if s.get("provisioned"):
                async with self.repo.transaction():
                    await self.repo.set_sandbox_status(str(s["id"]), "deleted")
        return {"status": "deleted", **result}
