from contextlib import asynccontextmanager
from unittest.mock import AsyncMock


class FakeTransactions:
    def __init__(self):
        self.session = AsyncMock()
        self.outbox = []
        self.events = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def enqueue(self, **values):
        self.outbox.append(values)
        return len(self.outbox)

    async def notify(self, **values):
        self.outbox.append(values)

    async def append_event(self, event, category):
        self.events.append((category, event))
        return f"{category}-{len(self.events)}"

    async def get_command(self, key):
        return next(
            (item["payload"] for item in self.outbox if item.get("key") == key), None
        )
