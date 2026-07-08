from __future__ import annotations

from typing import Protocol

from app.domain.agent.memory import AgentMemory, MemoryPolicy, MemoryWindow


class AgentMemoryRepository(Protocol):
    async def append(self, memory: AgentMemory) -> None:
        ...

    async def list_for_session(self, session_id: str) -> list[AgentMemory]:
        ...


class AgentMemoryService:
    def __init__(
        self,
        repository: AgentMemoryRepository,
        policy: MemoryPolicy,
    ):
        self.repository = repository
        self.policy = policy

    async def append(self, memory: AgentMemory) -> None:
        await self.repository.append(memory)

    async def build_window(self, session_id: str) -> MemoryWindow:
        memories = await self.repository.list_for_session(session_id)
        window = self.policy.select_window(memories)
        return MemoryWindow(memories=window, summary=self.policy.summarize(window))
