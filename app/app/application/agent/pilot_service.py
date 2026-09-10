from __future__ import annotations

from typing import cast
from uuid import UUID

from app.application.dto.pilot_runtime import (
    BrowserCitation,
    PilotCancelCommand,
    PilotCreateRun,
    PilotResumeCommand,
    PilotRunSnapshot,
    RequiredAction,
    RunStatus,
    SourceResolution,
)
from app.infrastructure.agent.pilot_repository import PilotRepository

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
        title = command.title or command.input.text.strip()[:120]
        run, _created = await self.repository.create_run(
            owner_actor_id=command.delegated_user.actor_id,
            idempotency_key=command.idempotency_key,
            title=title,
            user_input=command.input.text,
        )
        return await self.snapshot(
            run_id=UUID(str(run["id"])),
            owner_actor_id=command.delegated_user.actor_id,
        )

    async def snapshot(self, *, run_id: UUID, owner_actor_id: UUID) -> PilotRunSnapshot:
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
            elif event_type == "status" and data.get("status") in {
                "queued",
                "cancelled",
            }:
                required_action = None
            elif event_type == "failed":
                required_action = None
        last = events[-1] if events else None
        mapped_status = STATUS_MAP.get(str(run["status"]))
        if (
            run["status"] == "waiting"
            and run.get("resume_idempotency_key") is not None
            and not run.get("resume_token")
        ):
            mapped_status = "queued"
            required_action = None
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
        run, _consumed = await self.repository.consume_resume(
            run_id=run_id,
            owner_actor_id=command.delegated_user.actor_id,
            action_id=command.action_id,
            idempotency_key=command.idempotency_key,
            value=command.value,
        )
        return await self.snapshot(
            run_id=run_id,
            owner_actor_id=command.delegated_user.actor_id,
        )

    async def cancel(
        self, *, run_id: UUID, command: PilotCancelCommand
    ) -> PilotRunSnapshot:
        await self.repository.request_cancel(
            run_id=run_id,
            owner_actor_id=command.delegated_user.actor_id,
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
        return SourceResolution(location=f"/api/citation-sources/{evidence_id}")
