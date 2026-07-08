from __future__ import annotations

from typing import Any

from langgraph.types import Command

from app.application.agent.graph_runtime_service import GraphRuntimeResult, GraphRuntimeService


class LangGraphRuntimeService(GraphRuntimeService):
    def resume(self, graph: Any, user_input: str, *, session_id: str) -> GraphRuntimeResult:
        return self.stream_with_config(
            graph,
            Command(resume=user_input),
            self.build_config(session_id=session_id),
        )
