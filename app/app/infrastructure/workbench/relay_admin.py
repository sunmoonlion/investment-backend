"""会合点管理通道的 WebSocket 实现。"""

from __future__ import annotations

import json
from typing import Any

from app.application.workbench.provisioning import RelayAdminFailed
from app.domain.workbench.errors import WorkbenchError


class WsRelayAdmin:
    """会合点管理通道：一条短连接，hello 后发一条命令。"""

    def __init__(self, url: str, token: str) -> None:
        self.url = url.rstrip("/") + "/admin"
        self.token = token

    async def _send(
        self, message: dict[str, Any], *, expect: str = "ok"
    ) -> dict[str, Any]:
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
        if reply.get("type") != expect:
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

    async def agents(self) -> dict[str, dict[str, Any]]:
        reply = await self._send({"type": "agents"}, expect="agents")
        agents = reply.get("agents")
        if not isinstance(agents, dict):
            raise RelayAdminFailed("relay refused: malformed agents reply")
        return {
            str(user): dict(info)
            for user, info in agents.items()
            if isinstance(info, dict)
        }
