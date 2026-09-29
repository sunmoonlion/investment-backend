"""工作台的组装：把基础设施层的实现接到应用层的端口上。

应用层不认识数据库会话、WebSocket 客户端这些具体的东西；接口层、命令行、后台角色都从这里取接好的对象。
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.application.ports.workbench import (
    AppServerConnector,
    EventPublisher,
    Provisioner,
    RelayAdmin,
    WorkbenchStore,
    WorkbenchStores,
)
from app.application.workbench.runner import Runner
from app.infrastructure.workbench.app_server_client import WsAppServerConnector
from app.infrastructure.workbench.provisioner import HttpProvisioner, ProvisionerConfig
from app.infrastructure.workbench.publisher import RedisPublisher
from app.infrastructure.workbench.relay_admin import WsRelayAdmin
from app.infrastructure.workbench.repository import (
    SqlWorkbenchStores,
    WorkbenchRepository,
)


def workbench_store(session: AsyncSession) -> WorkbenchStore:
    """用调用方已有的数据库会话开一本账（网页接口的每个请求一个会话）。"""
    return WorkbenchRepository(session)


def workbench_stores(
    session_factory: async_sessionmaker[AsyncSession],
) -> WorkbenchStores:
    return SqlWorkbenchStores(session_factory)


def app_server_connector() -> AppServerConnector:
    return WsAppServerConnector()


def event_publisher(redis, prefix: str) -> EventPublisher:
    """`redis` 给 None 时什么都不发（测试、评测）。"""
    return RedisPublisher(redis, prefix)


def build_provisioner(
    *,
    url: str,
    token: str,
    model_provider: str = "",
    model: str = "",
    provider_base_url: str = "",
) -> Provisioner:
    return HttpProvisioner(
        ProvisionerConfig(
            url=url,
            token=token,
            model_provider=model_provider,
            model=model,
            provider_base_url=provider_base_url,
        )
    )


def build_relay_admin(url: str, token: str) -> RelayAdmin:
    return WsRelayAdmin(url, token)


def build_runner(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    publisher: EventPublisher,
    runner_id: str,
    environment_key: str = "user-pc",
    poll_seconds: float = 1.0,
    records: bool = False,
) -> Runner:
    return Runner(
        workbench_stores(session_factory),
        connector=app_server_connector(),
        publisher=publisher,
        runner_id=runner_id,
        environment_key=environment_key,
        poll_seconds=poll_seconds,
        records=records,
    )
