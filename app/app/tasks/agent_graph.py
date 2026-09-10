from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.infrastructure.graph.langgraph_runtime import LangGraphRuntimeService
from app.worker import celery_app

logger = logging.getLogger(__name__)
_worker_loop: asyncio.AbstractEventLoop | None = None


def _stream_graph(
    graph: Any, graph_input: Any, config: dict[str, Any]
) -> dict[str, Any]:
    return (
        LangGraphRuntimeService().stream_with_config(graph, graph_input, config).state
    )


def _run_in_worker_loop(coro: Any) -> Any:
    global _worker_loop
    if _worker_loop is None or _worker_loop.is_closed():
        _worker_loop = asyncio.new_event_loop()
        asyncio.set_event_loop(_worker_loop)
    return _worker_loop.run_until_complete(coro)


async def _run_agent_graph(
    run_id: str,
    user_input: str | None = None,
    security_context: dict | None = None,
) -> None:
    raise RuntimeError(
        "Direct graph dispatch is disabled; use the transactional Agent outbox"
    )


@celery_app.task(name="app.tasks.agent_graph.run")
def run_agent_graph(
    run_id: str,
    user_input: str | None = None,
    security_context: dict | None = None,
) -> None:
    _run_in_worker_loop(_run_agent_graph(run_id, user_input, security_context))
