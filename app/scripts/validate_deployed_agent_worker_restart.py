from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from typing import Any

import httpx

EXPECTED_TIMELINE = [
    "TimelineRunStarted",
    "TimelineWaitInputDisplayed",
    "TimelineUserInputReceived",
    "TimelineToolStarted",
    "TimelineToolCompleted",
    "TimelineRunCompleted",
]


def event_types(events: list[dict[str, Any]]) -> list[str]:
    return [str(event["type"]) for event in events]


def find_wait_event(events: list[dict[str, Any]]) -> dict[str, Any] | None:
    for event in events:
        if event.get("type") == "TimelineWaitInputDisplayed":
            return event
    return None


async def poll_events(
    client: httpx.AsyncClient,
    session_id: str,
    *,
    until_type: str,
    timeout_seconds: float,
) -> list[dict[str, Any]]:
    deadline = asyncio.get_running_loop().time() + timeout_seconds
    last_events: list[dict[str, Any]] = []
    while asyncio.get_running_loop().time() < deadline:
        response = await client.get(f"/api/agent/sessions/{session_id}/events")
        response.raise_for_status()
        last_events = response.json()["events"]
        if until_type in event_types(last_events):
            return last_events
        await asyncio.sleep(1.0)
    raise AssertionError(
        f"Timed out waiting for {until_type}; current timeline={event_types(last_events)!r}"
    )


async def run_kubectl(args: list[str], *, timeout_seconds: float) -> None:
    env = os.environ.copy()
    process = await asyncio.create_subprocess_exec(
        "kubectl",
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout_seconds)
    except TimeoutError as exc:
        process.kill()
        await process.communicate()
        raise AssertionError(f"kubectl timed out: {' '.join(args)}") from exc
    if process.returncode != 0:
        raise AssertionError(
            "kubectl failed: "
            f"{' '.join(args)}\nstdout={stdout.decode().strip()}\nstderr={stderr.decode().strip()}"
        )
    if stdout.strip():
        print(stdout.decode().strip(), flush=True)
    if stderr.strip():
        print(stderr.decode().strip(), flush=True)


async def validate_sse_replay(
    client: httpx.AsyncClient,
    session_id: str,
    first_event_id: str,
    expected_types: list[str],
) -> None:
    seen: list[str] = []
    async with client.stream(
        "GET",
        f"/api/agent/sessions/{session_id}/stream",
        params={"last_event_id": first_event_id},
        timeout=10.0,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("data: "):
                payload = json.loads(line.removeprefix("data: "))
                seen.append(str(payload["type"]))
                if seen == expected_types:
                    break
            if len(seen) > len(expected_types):
                break
    if seen != expected_types:
        raise AssertionError(
            f"SSE replay mismatch: expected={expected_types!r} actual={seen!r}"
        )


async def async_main(args: argparse.Namespace) -> None:
    async with httpx.AsyncClient(base_url=args.base_url, timeout=10.0) as client:
        print(json.dumps({"step": "create_session"}, ensure_ascii=False), flush=True)
        session_response = await client.post("/api/agent/sessions")
        session_response.raise_for_status()
        session_id = session_response.json()["session_id"]

        print(
            json.dumps({"step": "create_run", "session_id": session_id}, ensure_ascii=False),
            flush=True,
        )
        run_response = await client.post(
            f"/api/agent/sessions/{session_id}/runs",
            json={
                "idempotency_key": f"worker-restart-{uuid.uuid4()}",
                "agent_profile_key": "default_research",
                "user_input": {},
            },
        )
        run_response.raise_for_status()
        run_id = run_response.json()["run_id"]

        waiting_events = await poll_events(
            client,
            session_id,
            until_type="TimelineWaitInputDisplayed",
            timeout_seconds=args.timeout,
        )
        wait_event = find_wait_event(waiting_events)
        if wait_event is None:
            raise AssertionError("wait event missing")
        resume_token = wait_event["payload"]["resume_token"]

        print(
            json.dumps(
                {
                    "step": "restart_worker",
                    "namespace": args.namespace,
                    "deployment": args.deployment,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        await run_kubectl(
            ["rollout", "restart", f"deployment/{args.deployment}", "-n", args.namespace],
            timeout_seconds=args.timeout,
        )
        await run_kubectl(
            [
                "rollout",
                "status",
                f"deployment/{args.deployment}",
                "-n",
                args.namespace,
                f"--timeout={int(args.timeout)}s",
            ],
            timeout_seconds=args.timeout + 5.0,
        )

        print(
            json.dumps({"step": "resume_after_worker_restart", "run_id": run_id}, ensure_ascii=False),
            flush=True,
        )
        resume_response = await client.post(
            f"/api/agent/runs/{run_id}/resume",
            json={
                "resume_token": resume_token,
                "user_input": {"text": "continue after worker restart"},
                "idempotency_key": f"resume-worker-restart-{uuid.uuid4()}",
            },
        )
        resume_response.raise_for_status()

        completed_events = await poll_events(
            client,
            session_id,
            until_type="TimelineRunCompleted",
            timeout_seconds=args.timeout,
        )
        timeline = event_types(completed_events)
        if timeline != EXPECTED_TIMELINE:
            raise AssertionError(
                f"timeline mismatch: expected={EXPECTED_TIMELINE!r} actual={timeline!r}"
            )

        first_event_id = str(completed_events[0]["id"])
        replay_response = await client.get(
            f"/api/agent/sessions/{session_id}/events",
            params={"after_event_id": first_event_id},
        )
        replay_response.raise_for_status()
        replay_types = event_types(replay_response.json()["events"])
        if replay_types != EXPECTED_TIMELINE[1:]:
            raise AssertionError(
                f"HTTP replay mismatch: expected={EXPECTED_TIMELINE[1:]!r} actual={replay_types!r}"
            )

        await validate_sse_replay(
            client,
            session_id,
            first_event_id,
            EXPECTED_TIMELINE[1:],
        )

        print(
            json.dumps(
                {
                    "deployed_agent_worker_restart_validation": "ok",
                    "base_url": args.base_url,
                    "namespace": args.namespace,
                    "deployment": args.deployment,
                    "session_id": session_id,
                    "run_id": run_id,
                    "timeline": timeline,
                    "http_replay": replay_types,
                    "sse_replay": EXPECTED_TIMELINE[1:],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate deployed MoocManus v4 recovery after a Celery worker restart."
    )
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--namespace", default="app-platform-dev")
    parser.add_argument("--deployment", default="celeryworker-research-admin-backend")
    parser.add_argument("--timeout", type=float, default=120.0)
    return parser.parse_args()


def main() -> None:
    asyncio.run(async_main(parse_args()))


if __name__ == "__main__":
    main()
