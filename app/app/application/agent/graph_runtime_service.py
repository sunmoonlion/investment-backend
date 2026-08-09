from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class GraphRuntimeResult:
    state: dict[str, Any]
    interrupted: bool = False


class GraphRuntimeService:
    def build_config(self, *, session_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": session_id}}

    def run(
        self, graph: Any, graph_input: Any, *, session_id: str
    ) -> GraphRuntimeResult:
        return self.stream_with_config(
            graph,
            graph_input,
            self.build_config(session_id=session_id),
        )

    def resume(
        self, graph: Any, user_input: str, *, session_id: str
    ) -> GraphRuntimeResult:
        raise NotImplementedError(
            "Runtime adapters must translate resume input to their graph command type."
        )

    def stream_with_config(
        self,
        graph: Any,
        graph_input: Any,
        config: dict[str, Any],
    ) -> GraphRuntimeResult:
        state: dict[str, Any] = {}
        for chunk in graph.stream(graph_input, config=config):
            if "__interrupt__" in chunk:
                return GraphRuntimeResult(state=chunk, interrupted=True)
            for value in chunk.values():
                if isinstance(value, dict):
                    state.update(value)
        return GraphRuntimeResult(state=state)
