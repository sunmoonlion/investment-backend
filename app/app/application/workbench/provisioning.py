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

from app.application.workbench.tokens import TokenIssuer, jti_of
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


class SandboxCapacityFull(WorkbenchError):
    """F-SBX-08：全局沙箱数到上限。已有沙箱的用户不受影响（更新照常），新用户稍后再试。"""

    code = "sandbox_capacity_full"
    http_status = 503


class RelayAdminFailed(WorkbenchError):
    code = "relay_admin_failed"
    http_status = 502


class Cipher(Protocol):
    def encrypt(self, data: bytes) -> bytes: ...
    def decrypt(self, token: bytes) -> bytes: ...


class RelayAdmin(Protocol):
    async def set_tokens(self, user: str, agent: str, sandbox: str) -> None: ...
    async def revoke(self, user: str) -> None: ...
    async def set_public_key(self, pem: str) -> None: ...
    async def revoke_jti(self, jtis: list[str]) -> None: ...


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

    async def set_public_key(self, pem: str) -> None:
        """D10：把工作台验签公钥推到边缘（边缘不能回源取 JWKS）。"""
        await self._send({"type": "set_public_key", "pem": pem})

    async def revoke_jti(self, jtis: list[str]) -> None:
        await self._send({"type": "revoke_jti", "jtis": jtis})


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
        issuer: TokenIssuer | None = None,
        global_limit: int = 0,
    ) -> None:
        self.repo = repo
        self.cipher = cipher
        self.provisioner = provisioner
        self.relay_admin = relay_admin
        self.relay_public_url = relay_public_url
        self.codex_version = codex_version
        self.issuer = issuer  # D10：有签名密钥时发 JWT；否则不透明随机令牌
        self.global_limit = global_limit  # F-SBX-08；0 = 不设上限

    async def ensure_relay_identity(
        self, owner: str
    ) -> tuple[dict[str, Any], str | None]:
        """返回 (身份行, 新签发的代理令牌或 None)。令牌只在首次签发时明文返回。"""
        row = await self.repo.get_relay_identity(owner)
        fresh: str | None = None
        if row is None:
            row = await self._issue_identity(owner)
            fresh = row["_agent_token"]
        assert row is not None
        agent_token = self.cipher.decrypt(
            row["agent_token_ciphertext"].encode()
        ).decode()
        sandbox_token = self.cipher.decrypt(
            row["sandbox_token_ciphertext"].encode()
        ).decode()
        await self._register(row["relay_user"], agent_token, sandbox_token)
        async with self.repo.transaction():
            await self.repo.mark_relay_registered(owner)
        row["_sandbox_token"] = sandbox_token
        return row, fresh

    def _mint(self, relay_user: str) -> tuple[str, str]:
        if self.issuer is not None:
            return (
                self.issuer.agent_token(relay_user).token,
                self.issuer.sandbox_token(relay_user, sandbox=relay_user).token,
            )
        return secrets.token_urlsafe(32), secrets.token_urlsafe(32)

    async def _issue_identity(self, owner: str) -> dict[str, Any]:
        relay_user = relay_user_for(owner)
        agent, sandbox = self._mint(relay_user)
        async with self.repo.transaction():
            await self.repo.put_relay_identity(
                owner,
                relay_user=relay_user,
                agent_token_ciphertext=self.cipher.encrypt(agent.encode()).decode(),
                sandbox_token_ciphertext=self.cipher.encrypt(sandbox.encode()).decode(),
            )
        row = await self.repo.get_relay_identity(owner)
        assert row is not None
        row["_agent_token"] = agent
        return row

    async def _register(
        self, relay_user: str, agent_token: str, sandbox_token: str
    ) -> None:
        """D10：先推公钥（边缘验 JWT 用），再登记一份令牌——没有公钥的会合点（开发）靠登记表配对。"""
        if self.issuer is not None:
            await self.relay_admin.set_public_key(self.issuer.public_pem())
        await self.relay_admin.set_tokens(relay_user, agent_token, sandbox_token)

    async def rotate_relay_identity(self, owner: str) -> dict[str, Any]:
        """撤换：旧令牌按 jti 吊销（推到会合点，F-RELAY-06），签新的一对，沙箱在线就用新沙箱令牌滚动。
        新代理令牌只在这次响应里出现一次。"""
        old = await self.repo.get_relay_identity(owner)
        if old is None:
            raise NoCredential("no relay identity to rotate; provision a sandbox first")
        old_tokens = [
            self.cipher.decrypt(old[k].encode()).decode()
            for k in ("agent_token_ciphertext", "sandbox_token_ciphertext")
        ]
        jtis = [j for j in (jti_of(t) for t in old_tokens) if j]
        row = await self._issue_identity(owner)
        if jtis:
            await self.relay_admin.revoke_jti(jtis)
        else:
            await self.relay_admin.revoke(row["relay_user"])
        sandbox_token = self.cipher.decrypt(
            row["sandbox_token_ciphertext"].encode()
        ).decode()
        await self._register(row["relay_user"], row["_agent_token"], sandbox_token)
        async with self.repo.transaction():
            await self.repo.mark_relay_registered(owner)
        live = await self.provisioner.status(row["relay_user"])
        if live.get("status") not in (None, "absent", "deleted"):
            credential = await self.repo.active_credential(owner)
            if credential is not None:
                await self._upsert_sandbox(owner, row, sandbox_token, credential)
        return {
            "relay": {
                "url": self.relay_public_url,
                "user": row["relay_user"],
                "agent_token": row["_agent_token"],
            },
            "revoked": len(jtis) or 2,
            "sandbox_rolled": live.get("status") not in (None, "absent", "deleted"),
        }

    async def _check_capacity(self, owner: str) -> None:
        """F-SBX-08：只挡"新增一个沙箱"；已有沙箱（在跑或启动中）的用户更新不受限。"""
        if not self.global_limit or await self.repo.has_live_provisioned_sandbox(owner):
            return
        live = await self.repo.count_live_provisioned_sandboxes()
        if live >= self.global_limit:
            log.warning(
                "sandbox capacity full live=%s limit=%s owner=%s",
                live,
                self.global_limit,
                owner,
            )
            raise SandboxCapacityFull(
                "sandbox capacity is full, please try again later"
            )

    async def _upsert_sandbox(
        self,
        owner: str,
        identity: dict[str, Any],
        sandbox_token: str,
        credential: dict[str, Any],
    ) -> tuple[str, dict[str, Any]]:
        model_key = self.cipher.decrypt(credential["ciphertext"].encode()).decode()
        spec = {
            "model_provider": self.provisioner.config.model_provider,
            "model": self.provisioner.config.model,
            "provider_base_url": self.provisioner.config.provider_base_url,
            "model_key": model_key,
            "relay_token": sandbox_token,
            "relay_user": identity["relay_user"],
        }
        if self.issuer is not None:
            spec["knowledge_mcp_token"] = self.issuer.knowledge_token(
                identity["relay_user"], sandbox=identity["relay_user"]
            ).token
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
        return sb_id, result

    async def provision(self, owner: str) -> dict[str, Any]:
        credential = await self.repo.active_credential(owner)
        if credential is None:
            raise NoCredential("register a model key in settings first")
        await self._check_capacity(owner)
        identity, fresh_agent_token = await self.ensure_relay_identity(owner)
        sb_id, result = await self._upsert_sandbox(
            owner, identity, identity["_sandbox_token"], credential
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
