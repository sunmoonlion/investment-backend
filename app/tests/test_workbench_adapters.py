"""工作台在基础设施层的三个小实现：会合点管理通道、事件发布、组装。"""

from __future__ import annotations

import json
import socket

import pytest
from websockets.asyncio.server import serve

from app.application.workbench.provisioning import RelayAdminFailed
from app.bootstrap.workbench import (
    build_provisioner,
    build_relay_admin,
    event_publisher,
)
from app.infrastructure.workbench.provisioner import HttpProvisioner
from app.infrastructure.workbench.publisher import RedisPublisher
from app.infrastructure.workbench.relay_admin import WsRelayAdmin


class Relay:
    """会合点管理通道的替身：hello → welcome，一条命令 → 一条答复。"""

    def __init__(self, *, token: str = "admin-token", refuse: str | None = None):
        self.token = token
        self.refuse = refuse
        self.paths: list[str] = []
        self.commands: list[dict] = []

    async def handle(self, ws) -> None:
        self.paths.append(ws.request.path)
        hello = json.loads(await ws.recv())
        if hello != {"type": "hello", "role": "admin", "token": self.token}:
            await ws.send(json.dumps({"type": "error", "reason": "unauthorized"}))
            return
        await ws.send(json.dumps({"type": "welcome"}))
        self.commands.append(json.loads(await ws.recv()))
        if self.refuse:
            await ws.send(json.dumps({"type": "error", "reason": self.refuse}))
        else:
            await ws.send(json.dumps({"type": "ok"}))


async def test_relay_admin_says_hello_then_sends_exactly_one_command():
    relay = Relay()
    async with serve(relay.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        admin = WsRelayAdmin(f"ws://127.0.0.1:{port}/", "admin-token")
        await admin.set_tokens("u-1", "agent", "sandbox")
        await admin.revoke("u-1")
        await admin.set_public_key("PEM")
        await admin.revoke_jti(["a", "b"])
    assert relay.paths == ["/admin"] * 4
    assert relay.commands == [
        {"type": "set_tokens", "user": "u-1", "agent": "agent", "sandbox": "sandbox"},
        {"type": "revoke", "user": "u-1"},
        {"type": "set_public_key", "pem": "PEM"},
        {"type": "revoke_jti", "jtis": ["a", "b"]},
    ]


async def test_relay_admin_asks_who_is_online():
    online = {"u-1": {"codex": "0.155.1", "machine": {"name": "pc"}}, "u-2": "junk"}

    async def handle(ws) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "welcome"}))
        assert json.loads(await ws.recv()) == {"type": "agents"}
        await ws.send(json.dumps({"type": "agents", "agents": online}))

    async with serve(handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        admin = WsRelayAdmin(f"ws://127.0.0.1:{port}", "admin-token")
        assert await admin.agents() == {"u-1": online["u-1"]}

    async def old_relay(ws) -> None:
        await ws.recv()
        await ws.send(json.dumps({"type": "welcome"}))
        await ws.recv()
        await ws.send(
            json.dumps({"type": "error", "reason": "unknown admin message agents"})
        )

    async with serve(old_relay, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        admin = WsRelayAdmin(f"ws://127.0.0.1:{port}", "admin-token")
        with pytest.raises(RelayAdminFailed, match="unknown admin message"):
            await admin.agents()


async def test_relay_admin_failures_are_reported_without_the_token():
    relay = Relay(token="the-right-one")
    async with serve(relay.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(RelayAdminFailed, match="auth failed") as wrong:
            await WsRelayAdmin(f"ws://127.0.0.1:{port}", "wrong-secret").revoke("u-1")
    assert "wrong-secret" not in str(wrong.value)
    assert relay.commands == []

    refusing = Relay(refuse="unknown user")
    async with serve(refusing.handle, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        with pytest.raises(RelayAdminFailed, match="relay refused: unknown user"):
            await WsRelayAdmin(f"ws://127.0.0.1:{port}", "admin-token").revoke("u-1")

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        closed_port = probe.getsockname()[1]
    with pytest.raises(RelayAdminFailed, match="unreachable"):
        await WsRelayAdmin(f"ws://127.0.0.1:{closed_port}", "admin-token").revoke("u")


class RecordingRedis:
    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.sent: list[tuple[str, str]] = []

    async def publish(self, channel: str, message: str) -> int:
        if self.fail:
            raise ConnectionError("redis is down")
        self.sent.append((channel, message))
        return 1


async def test_publisher_names_channels_and_never_raises():
    redis = RecordingRedis()
    publisher = RedisPublisher(redis, "inv:wb")
    assert publisher.session_channel("s1") == "inv:wb:session:s1:events"
    assert publisher.commands_channel() == "inv:wb:commands"
    await publisher.publish(publisher.session_channel("s1"), {"seq": 1, "文": "中"})
    assert redis.sent == [("inv:wb:session:s1:events", '{"seq": 1, "文": "中"}')]

    await RedisPublisher(RecordingRedis(fail=True), "p").publish("c", {"seq": 2})
    await RedisPublisher(None, "p").publish("c", {"seq": 3})


def test_wiring_returns_the_real_implementations():
    assert type(event_publisher(None, "p")) is RedisPublisher
    assert type(build_relay_admin("ws://relay.example.test", "t")) is WsRelayAdmin
    provisioner = build_provisioner(
        url="http://provisioner.example.test",
        token="t",
        model_provider="kimi",
        model="k3",
        provider_base_url="https://model.example.test/v1",
    )
    assert type(provisioner) is HttpProvisioner
    assert (
        provisioner.config.model_provider,
        provisioner.config.model,
        provisioner.config.provider_base_url,
    ) == ("kimi", "k3", "https://model.example.test/v1")
