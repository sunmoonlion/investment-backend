from __future__ import annotations

from typing import cast
from uuid import UUID

from app.infrastructure.agent.pilot_repository import PilotRepository
from app.infrastructure.messaging.celery_producer import get_celery_producer
from app.interfaces.schemas.pilot_runtime import (
    BrowserCitation,
    PilotCancelCommand,
    PilotCreateRun,
    PilotResumeCommand,
    PilotRunSnapshot,
    RequiredAction,
    RunStatus,
    SourceResolution,
)

STATUS_MAP = {
    "created": "queued",
    "running": "running",
    "waiting": "waiting_for_input",
    "completed": "succeeded",
    "failed": "failed",
    "cancelled": "cancelled",
    "budget_exceeded": "failed",
}


class PilotService:
    def __init__(self, repository: PilotRepository):
        self.repository = repository

    async def create_run(self, command: PilotCreateRun) -> PilotRunSnapshot:
        producer = get_celery_producer()
        if not producer.enabled:
            raise RuntimeError("pilot Runtime worker transport is unavailable")
        title = command.title or command.input.text.strip()[:120]
        run, created = await self.repository.create_run(
            owner_actor_id=command.delegated_user.actor_id,
            idempotency_key=command.idempotency_key,
            title=title,
            user_input=command.input.text,
        )
        if created:
            try:
                producer.dispatch_pilot_graph(str(run["id"]))
            except Exception:
                await self.repository.set_status(
                    run_id=UUID(str(run["id"])),
                    session_id=UUID(str(run["session_id"])),
                    status="failed",
                    error="dispatch_failed",
                )
                await self.repository.append_browser_event(
                    run_id=UUID(str(run["id"])),
                    session_id=UUID(str(run["session_id"])),
                    event_type="failed",
                    data={
                        "code": "dispatch_failed",
                        "message": "The pilot worker transport rejected the run.",
                    },
                )
                raise
        return await self.snapshot(
            run_id=UUID(str(run["id"])),
            owner_actor_id=command.delegated_user.actor_id,
        )

    async def snapshot(
        self, *, run_id: UUID, owner_actor_id: UUID
    ) -> PilotRunSnapshot:
        run = await self.repository.get_run(
            run_id=run_id, owner_actor_id=owner_actor_id
        )
        if run is None:
            raise PermissionError("pilot run belongs to another actor")
        events = await self.repository.list_browser_events(
            run_id=run_id, owner_actor_id=owner_actor_id
        )
        summary: str | None = None
        citations: dict[UUID, BrowserCitation] = {}
        required_action: RequiredAction | None = None
        for event in events:
            event_type = event["type"]
            data = event["data"]
            if event_type == "citation":
                citation = BrowserCitation.model_validate(data["citation"])
                citations[citation.evidence_id] = citation
            elif event_type == "input_required":
                required_action = RequiredAction.model_validate(data["action"])
            elif event_type == "completed":
                summary = str(data.get("summary") or "")
                required_action = None
            elif event_type == "failed":
                required_action = None
        last = events[-1] if events else None
        mapped_status = STATUS_MAP.get(str(run["status"]))
        if mapped_status is None:
            raise RuntimeError("pilot run contains an unknown status")
        return PilotRunSnapshot(
            run_id=run_id,
            title=str(run["title"]),
            status=cast(RunStatus, mapped_status),
            summary=summary,
            last_sequence_no=int(last["sequence_no"]) if last else 0,
            last_event_id=UUID(str(last["event_id"])) if last else None,
            citations=tuple(citations.values()),
            required_action=required_action,
            updated_at=run["updated_at"],
        )

    async def resume(
        self, *, run_id: UUID, command: PilotResumeCommand
    ) -> PilotRunSnapshot:
        run, consumed = await self.repository.consume_resume(
            run_id=run_id,
            owner_actor_id=command.delegated_user.actor_id,
            action_id=command.action_id,
            idempotency_key=command.idempotency_key,
        )
        if consumed:
            producer = get_celery_producer()
            if not producer.enabled:
                await self._fail_consumed_resume(run_id=run_id, run=run)
                raise RuntimeError(
                    "pilot Runtime worker transport is unavailable"
                )
            try:
                producer.dispatch_pilot_graph(
                    run_id=str(run_id), resume=command.value
                )
            except Exception:
                await self._fail_consumed_resume(run_id=run_id, run=run)
                raise
        return await self.snapshot(
            run_id=run_id,
            owner_actor_id=command.delegated_user.actor_id,
        )

    async def _fail_consumed_resume(self, *, run_id: UUID, run: dict) -> None:
        """Make a consumed-but-undispatched resume terminal and observable.

        Once the action token is atomically consumed it must never become
        reusable.  A transport failure therefore cannot leave the run in a
        misleading ``waiting`` state.
        """

        session_id = UUID(str(run["session_id"]))
        await self.repository.set_status(
            run_id=run_id,
            session_id=session_id,
            status="failed",
            error="resume_dispatch_failed",
        )
        await self.repository.append_browser_event(
            run_id=run_id,
            session_id=session_id,
            event_type="failed",
            data={
                "code": "resume_dispatch_failed",
                "message": "The pilot worker transport rejected the resume.",
            },
        )

    async def cancel(
        self, *, run_id: UUID, command: PilotCancelCommand
    ) -> PilotRunSnapshot:
        run = await self.repository.request_cancel(
            run_id=run_id,
            owner_actor_id=command.delegated_user.actor_id,
        )
        if run["status"] == "cancelled":
            events = await self.repository.list_browser_events(
                run_id=run_id,
                owner_actor_id=command.delegated_user.actor_id,
            )
            if (
                not events
                or events[-1]["type"] != "status"
                or events[-1].get("data", {}).get("status") != "cancelled"
            ):
                await self.repository.append_browser_event(
                    run_id=run_id,
                    session_id=run["session_id"],
                    event_type="status",
                    data={"status": "cancelled"},
                )
        return await self.snapshot(
            run_id=run_id,
            owner_actor_id=command.delegated_user.actor_id,
        )

    async def events(
        self,
        *,
        run_id: UUID,
        owner_actor_id: UUID,
        after_event_id: UUID | None,
    ) -> list[dict]:
        return await self.repository.list_browser_events(
            run_id=run_id,
            owner_actor_id=owner_actor_id,
            after_event_id=after_event_id,
        )

    async def citation_source(
        self,
        *,
        evidence_id: UUID,
        owner_actor_id: UUID,
    ) -> SourceResolution:
        await self.repository.assert_citation_owner(
            evidence_id=evidence_id,
            owner_actor_id=owner_actor_id,
        )
        # The pilot source view stays on the browser BFF origin.  The Web BFF
        # will fetch the authorized descriptor; it never redirects to a
        # provider-controlled URI.
        return SourceResolution(
            location=f"/api/citation-sources/{evidence_id}"
        )
