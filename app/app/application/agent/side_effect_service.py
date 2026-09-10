from __future__ import annotations

from typing import Any, Protocol


class ToolSideEffectStore(Protocol):
    async def record_side_effect_once(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool: ...


class ToolSideEffectService:
    def __init__(self, store: ToolSideEffectStore):
        self.store = store

    async def record_once(
        self,
        *,
        tool_call_id: str,
        run_id: str,
        result: dict[str, Any] | None = None,
    ) -> bool:
        return await self.store.record_side_effect_once(
            tool_call_id=tool_call_id,
            run_id=run_id,
            result=result,
        )


class UnknownSideEffect(RuntimeError):
    """The remote outcome must be reconciled before another execution is allowed."""


class RemoteEffectPort(Protocol):
    # The implementation must enforce the fencing token at the mutation boundary
    # and keep a stable receipt keyed by this business operation, across retries.
    async def execute(self, *, key: str, intent: dict, fencing_token: int) -> dict: ...
    async def lookup(self, *, key: str) -> dict | None: ...


class DurableSideEffectService:
    def __init__(self, repository, remote: RemoteEffectPort):
        self.repository = repository
        self.remote = remote

    async def execute(self, *, key: str, run_id: str, intent: dict) -> dict:
        item = await self.repository.prepare(key=key, run_id=run_id, intent=intent)
        if item["status"] == "completed":
            return item["receipt"] or item["result"]
        if item["status"] == "failed":
            raise RuntimeError("side effect has a confirmed failure")
        if item["status"] in {"executing", "unknown"}:
            return await self.reconcile(key=key)
        if not await self.repository.begin(key):
            return await self.reconcile(key=key)
        try:
            receipt = await self.remote.execute(
                key=key, intent=intent, fencing_token=self.repository.lease.epoch
            )
        except BaseException:
            # A failed transport does not prove that the remote mutation failed.
            # If the lease is gone, the periodic reconciler marks it unknown.
            from app.infrastructure.agent.transactions import LeaseLost

            try:
                await self.repository.settle(key, status="unknown")
            except LeaseLost:
                pass
            raise
        await self.repository.settle(key, status="completed", receipt=receipt)
        return receipt

    async def reconcile(self, *, key: str) -> dict:
        receipt = await self.remote.lookup(key=key)
        if receipt is None:
            raise UnknownSideEffect(f"remote outcome is not known for {key}")
        await self.repository.settle(key, status="completed", receipt=receipt)
        return receipt
