"""Adapter for the two existing graphs; no new agent loop.

Resume imports the last *accepted* graph state into an attempt-specific thread.
These graphs have only pure nodes. Late checkpoint writes stay in the old
attempt's namespace and cannot replace the accepted PostgreSQL binding.
"""

from __future__ import annotations

import asyncio
from contextlib import contextmanager
from typing import Any

from app.domain.agent.executor import ExecutionBinding, ExecutionEvent, ExecutionRequest


class GraphExecutor:
    def __init__(
        self, builder, *, provider: str, resume_field: str, checkpointer_factory=None
    ):
        self.builder = builder
        self.provider = provider
        self.resume_field = resume_field
        self.checkpointer_factory = checkpointer_factory or self._memory_checkpointer
        self._tasks: dict[str, asyncio.Task] = {}
        self._cancelled: set[str] = set()

    @staticmethod
    @contextmanager
    def _memory_checkpointer():
        from langgraph.checkpoint.memory import InMemorySaver

        yield InMemorySaver()

    async def start(self, request: ExecutionRequest) -> ExecutionBinding:
        if request.execution_id in self._tasks:
            raise ValueError("execution already started")
        binding = ExecutionBinding(
            execution_id=request.execution_id,
            provider=self.provider,
            state=request.input,
        )
        self._tasks[binding.execution_id] = asyncio.create_task(
            asyncio.to_thread(self._run, binding)
        )
        return binding

    def _run(self, binding: ExecutionBinding) -> ExecutionBinding:
        with self.checkpointer_factory() as saver:
            graph = self.builder(checkpointer=saver)
            config = {"configurable": {"thread_id": binding.execution_id}}
            interrupted = False
            for chunk in graph.stream(binding.state, config=config):
                if "__interrupt__" in chunk:
                    interrupted = True
            state: dict[str, Any] = dict(graph.get_state(config).values)
        return binding.model_copy(
            update={"state": state, "status": "waiting" if interrupted else "completed"}
        )

    async def resume(
        self, binding: ExecutionBinding, value: str, *, execution_id: str
    ) -> ExecutionBinding:
        if binding.provider != self.provider or binding.status != "waiting":
            raise ValueError("binding cannot be resumed by this executor")
        return await self.start(
            ExecutionRequest(
                execution_id=execution_id,
                input={**binding.state, self.resume_field: value},
            )
        )

    async def inspect(self, binding: ExecutionBinding) -> ExecutionBinding:
        if binding.execution_id in self._cancelled:
            return binding.model_copy(update={"status": "cancelled"})
        task = self._tasks.get(binding.execution_id)
        if task is not None and task.done():
            return task.result()
        return binding

    async def events(self, binding: ExecutionBinding, *, after: int = 0):
        if after < 0:
            raise ValueError("invalid execution cursor")
        task = self._tasks.get(binding.execution_id)
        result = await asyncio.shield(task) if task else binding
        if binding.execution_id in self._cancelled:
            result = result.model_copy(update={"status": "cancelled"})
        if after < 1:
            yield ExecutionEvent(sequence=1, binding=result)

    async def cancel(self, binding: ExecutionBinding) -> ExecutionBinding:
        # This adapter only runs pure graph nodes. Python threads are not killed;
        # cancellation revokes acceptance, and fenced writes enforce that boundary.
        self._cancelled.add(binding.execution_id)
        return binding.model_copy(update={"status": "cancelled"})

    async def close(self, binding: ExecutionBinding) -> None:
        task = self._tasks.pop(binding.execution_id, None)
        if task:
            try:
                await task  # joins the operation and closes its checkpointer
            finally:
                self._cancelled.discard(binding.execution_id)
