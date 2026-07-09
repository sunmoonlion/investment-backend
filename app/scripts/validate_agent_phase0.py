from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import httpx
from langgraph.types import Command
from sqlalchemy import text

from app.application.agent.run_service import AgentRunService
from app.domain.agent.commands import CreateRunCommand, ResumeRunCommand
from app.domain.agent.models import UserInput
from app.infrastructure.agent.repositories import AgentRepository
from app.infrastructure.graph.checkpointer import phase0_postgres_checkpointer
from app.infrastructure.graph.walking_skeleton import build_walking_skeleton_graph
from app.infrastructure.storage.postgres import get_postgres
from app.infrastructure.storage.redis import get_redis
from app.tasks.agent_graph import _run_agent_graph, _stream_graph


EXPECTED_TIMELINE = [
    "TimelineRunStarted",
    "TimelineWaitInputDisplayed",
    "TimelineUserInputReceived",
    "TimelineToolStarted",
    "TimelineToolCompleted",
    "TimelineRunCompleted",
]


@dataclass
class ValidationResult:
    session_id: str
    run_id: str
    first_event_id: str
    side_effect_count: int
    timeline: list[str]


def assert_equal(actual: Any, expected: Any, message: str) -> None:
    if actual != expected:
        raise AssertionError(f"{message}: expected={expected!r} actual={actual!r}")


def validate_checkpoint_resume() -> None:
    print(json.dumps({"step": "checkpoint_resume:start"}, ensure_ascii=False), flush=True)
    thread_id = str(uuid.uuid4())
    config = {"configurable": {"thread_id": thread_id}}
    with phase0_postgres_checkpointer() as checkpointer:
        graph = build_walking_skeleton_graph(checkpointer=checkpointer)
        first = _stream_graph(
            graph,
            {"session_id": "phase0-checkpoint", "run_id": "phase0-checkpoint"},
            config,
        )
    assert "__interrupt__" in first, "checkpoint graph did not interrupt"

    with phase0_postgres_checkpointer() as checkpointer:
        graph = build_walking_skeleton_graph(checkpointer=checkpointer)
        resumed = _stream_graph(graph, Command(resume="after-reopen"), config)
    assert_equal(
        resumed,
        {"user_input": "after-reopen", "side_effect_done": True},
        "checkpoint resume result mismatch",
    )
    print(json.dumps({"checkpoint_resume": "ok", "thread_id": thread_id}, ensure_ascii=False))


async def count_side_effects(run_id: str) -> int:
    async with get_postgres().session_factory() as session:
        result = await session.execute(
            text(
                """
                select count(*)
                from tool_side_effects
                where tool_call_id = :tool_call_id
                """
            ),
            {"tool_call_id": f"phase0:{run_id}:side_effect"},
        )
        return int(result.scalar_one())


async def validate_run_flow(*, replay: bool) -> ValidationResult:
    print(json.dumps({"step": "run_flow:create"}, ensure_ascii=False), flush=True)
    key = f"phase0-validation-{uuid.uuid4()}"
    async with get_postgres().session_factory() as session:
        repository = AgentRepository(session)
        service = AgentRunService(repository)
        session_id = await service.create_session()
        run = await service.create_run(
            CreateRunCommand(
                session_id=session_id,
                user_input=UserInput(),
                idempotency_key=key,
                agent_profile_key="default_research",
            )
        )
        run_id = str(run["run_id"])

    print(json.dumps({"step": "run_flow:first_graph", "run_id": run_id}, ensure_ascii=False), flush=True)
    await _run_agent_graph(run_id)

    print(json.dumps({"step": "run_flow:resume", "run_id": run_id}, ensure_ascii=False), flush=True)
    async with get_postgres().session_factory() as session:
        repository = AgentRepository(session)
        waiting = await repository.get_run(run_id)
        if not waiting:
            raise AssertionError("created run disappeared")
        assert_equal(waiting["status"], "waiting", "run should wait after first graph pass")
        events_after_wait = await repository.list_ui_events(session_id=session_id)
        assert_equal(
            [event["type"] for event in events_after_wait],
            ["TimelineRunStarted", "TimelineWaitInputDisplayed"],
            "waiting timeline mismatch",
        )
        resume_token = str(waiting["resume_token"])
        service = AgentRunService(repository)
        await service.resume_run(
            ResumeRunCommand(
                run_id=run_id,
                resume_token=resume_token,
                user_input=UserInput(text="continue"),
                idempotency_key="phase0-resume",
            )
        )

    print(json.dumps({"step": "run_flow:second_graph", "run_id": run_id}, ensure_ascii=False), flush=True)
    await _run_agent_graph(run_id, "continue")
    if replay:
        print(json.dumps({"step": "run_flow:forced_replay", "run_id": run_id}, ensure_ascii=False), flush=True)
        await _run_agent_graph(run_id, "continue")

    print(json.dumps({"step": "run_flow:assert", "run_id": run_id}, ensure_ascii=False), flush=True)
    async with get_postgres().session_factory() as session:
        repository = AgentRepository(session)
        done = await repository.get_run(run_id)
        if not done:
            raise AssertionError("completed run disappeared")
        assert_equal(done["status"], "completed", "run should complete after resume")
        events = await repository.list_ui_events(session_id=session_id)
        first_event_id = str(events[0]["id"])
        replayed = await repository.list_ui_events(
            session_id=session_id,
            after_event_id=first_event_id,
        )

    timeline = [event["type"] for event in events]
    assert_equal(timeline, EXPECTED_TIMELINE, "timeline mismatch")
    assert_equal(
        [event["type"] for event in replayed],
        timeline[1:],
        "last_event_id replay mismatch",
    )

    side_effect_count = await count_side_effects(run_id)
    assert_equal(side_effect_count, 1, "side effect should be written once")

    return ValidationResult(
        session_id=session_id,
        run_id=run_id,
        first_event_id=first_event_id,
        side_effect_count=side_effect_count,
        timeline=timeline,
    )


async def validate_http_replay(result: ValidationResult) -> None:
    print(json.dumps({"step": "http_replay:start", "session_id": result.session_id}, ensure_ascii=False), flush=True)
    os.environ["AGENT_V4_TRAFFIC_ENABLED"] = "true"
    from core.config import get_settings

    get_settings.cache_clear()
    from app.main import app

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="http://phase0.local",
        timeout=5.0,
    ) as client:
        events_response = await client.get(f"/api/agent/sessions/{result.session_id}/events")
        events_response.raise_for_status()
        all_events = events_response.json()["events"]
        assert_equal(
            [event["type"] for event in all_events],
            result.timeline,
            "HTTP events full replay mismatch",
        )

        replay_response = await client.get(
            f"/api/agent/sessions/{result.session_id}/events",
            params={"after_event_id": result.first_event_id},
        )
        replay_response.raise_for_status()
        replayed = replay_response.json()["events"]
        assert_equal(
            [event["type"] for event in replayed],
            result.timeline[1:],
            "HTTP events cursor replay mismatch",
        )

    print(
        json.dumps(
            {
                "http_events_replay": "ok",
                "session_id": result.session_id,
                "note": "SSE stream is intentionally left to network-level disconnect/reconnect tests.",
            },
            ensure_ascii=False,
        )
    )


async def async_main(args: argparse.Namespace) -> None:
    print(json.dumps({"step": "storage:postgres_init"}, ensure_ascii=False), flush=True)
    await get_postgres().init()
    print(json.dumps({"step": "storage:redis_init"}, ensure_ascii=False), flush=True)
    await get_redis().init()
    print(json.dumps({"step": "storage:ready"}, ensure_ascii=False), flush=True)
    try:
        if not args.skip_checkpoint:
            validate_checkpoint_resume()
        result = await validate_run_flow(replay=not args.skip_replay)
        if not args.skip_http:
            await validate_http_replay(result)
        print(
            json.dumps(
                {
                    "phase0_validation": "ok",
                    "session_id": result.session_id,
                    "run_id": result.run_id,
                    "first_event_id": result.first_event_id,
                    "side_effect_count": result.side_effect_count,
                    "timeline": result.timeline,
                },
                ensure_ascii=False,
            )
        )
    finally:
        await get_redis().shutdown()
        await get_postgres().shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate MoocManus v4 Phase 0.")
    parser.add_argument(
        "--skip-checkpoint",
        action="store_true",
        help="Skip the cross-connection LangGraph checkpoint resume check.",
    )
    parser.add_argument(
        "--skip-replay",
        action="store_true",
        help="Skip forced replay after completion.",
    )
    parser.add_argument(
        "--skip-http",
        action="store_true",
        help="Skip ASGI-level /events replay checks.",
    )
    return parser.parse_args()


def main() -> None:
    asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    main()
