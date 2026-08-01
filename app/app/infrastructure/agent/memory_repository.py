from __future__ import annotations

from collections import defaultdict

from app.domain.agent.memory import AgentMemory


class InMemoryAgentMemoryRepository:
    def __init__(self) -> None:
        self._by_session: dict[str, list[AgentMemory]] = defaultdict(list)

    async def append(self, memory: AgentMemory) -> None:
        self._by_session[memory.session_id].append(memory)

    async def list_for_session(self, session_id: str) -> list[AgentMemory]:
        return list(self._by_session[session_id])
