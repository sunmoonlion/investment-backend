from __future__ import annotations

import json
import multiprocessing
import queue
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import psycopg
from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.types import Command
from psycopg.types.json import Jsonb

from app.infrastructure.graph.checkpointer import (
    get_psycopg_database_url,
    phase0_postgres_checkpointer,
)
from app.infrastructure.graph.runtime_selection_spike import (
    RuntimeSpikeState,
    build_runtime_selection_spike_graph,
)


KillPoint = Literal["before_commit", "after_commit"]


@dataclass
class PostgresRuntimeControlPlane:
    database_url: str

    def create_run(self, *, thread_id: str, run_id: str, graph_version: str) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            row = connection.execute(
                """
                select thread_id, graph_version
                from agent_runs
                where id = %s
                """,
                (run_id,),
            ).fetchone()
        if not row:
            raise RuntimeError(f"runtime spike run missing: {run_id}")
        if str(row[0]) != thread_id or str(row[1]) != graph_version:
            raise RuntimeError("runtime spike run binding changed")

    def mark_running(self, run_id: str) -> None:
        self._set_status_unless_cancelled(run_id, "running")

    def mark_waiting(self, run_id: str) -> None:
        self._set_status_unless_cancelled(run_id, "waiting")

    def request_cancel(self, run_id: str) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            connection.execute(
                """
                update agent_runs
                set status = 'cancelled', updated_at = now()
                where id = %s
                  and status not in ('completed', 'failed', 'cancelled')
                """,
                (run_id,),
            )

    def raise_if_cancelled(self, run_id: str) -> None:
        if self.status(run_id) == "cancelled":
            from app.infrastructure.graph.runtime_selection_spike import (
                RuntimeSpikeCancelled,
            )

            raise RuntimeSpikeCancelled(f"run cancelled: {run_id}")

    def complete(self, run_id: str) -> None:
        self.raise_if_cancelled(run_id)
        self._set_status_unless_cancelled(run_id, "completed")

    def status(self, run_id: str) -> str:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            row = connection.execute(
                "select status from agent_runs where id = %s",
                (run_id,),
            ).fetchone()
        if not row:
            raise RuntimeError(f"runtime spike run missing: {run_id}")
        return str(row[0])

    def _set_status_unless_cancelled(self, run_id: str, status: str) -> None:
        with psycopg.connect(self.database_url, autocommit=True) as connection:
            connection.execute(
                """
                update agent_runs
                set status = %s, updated_at = now()
                where id = %s and status <> 'cancelled'
                """,
                (status, run_id),
            )


@dataclass
class PostgresIdempotencyLedger:
    database_url: str
    run_id: str
    kill_point: KillPoint | None = None
    marker_queue: Any | None = None
    wait_for_cancel: bool = False

    def execute_once(self, operation_id: str) -> str:
        result = f"effect:{operation_id}"
        if self.wait_for_cancel:
            self._wait_until_cancelled()
        if self.kill_point == "before_commit":
            self._mark_and_wait("before_commit")

        with psycopg.connect(self.database_url, autocommit=True) as connection:
            inserted = connection.execute(
                """
                insert into tool_side_effects (tool_call_id, run_id, status, result)
                values (%s, %s, 'completed', %s)
                on conflict (tool_call_id) do nothing
                returning tool_call_id
                """,
                (operation_id, self.run_id, Jsonb({"value": result})),
            ).fetchone()
            stored = connection.execute(
                """
                select result
                from tool_side_effects
                where tool_call_id = %s
                """,
                (operation_id,),
            ).fetchone()

        if inserted and self.kill_point == "after_commit":
            self._mark_and_wait("after_commit")
        if not stored:
            raise RuntimeError(f"durable operation journal missing: {operation_id}")
        return str(stored[0]["value"])

    def _wait_until_cancelled(self) -> None:
        if self.marker_queue is None:
            raise RuntimeError("cancel marker queue is required")
        self.marker_queue.put("running")
        control_plane = PostgresRuntimeControlPlane(self.database_url)
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            control_plane.raise_if_cancelled(self.run_id)
            time.sleep(0.1)
        raise RuntimeError("timed out waiting for cancellation")

    def _mark_and_wait(self, marker: str) -> None:
        if self.marker_queue is None:
            raise RuntimeError("kill marker queue is required")
        self.marker_queue.put(marker)
        while True:
            time.sleep(1)


def runtime_config(thread_id: str) -> RunnableConfig:
    return {"configurable": {"thread_id": thread_id}}


def initial_state(thread_id: str, run_id: str) -> RuntimeSpikeState:
    return {
        "thread_id": thread_id,
        "run_id": run_id,
        "user_input": "runtime SIGKILL comparison",
    }


def create_spike_run(database_url: str, *, thread_id: str, run_id: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            """
            insert into agent_sessions (id, status)
            values (%s, 'created')
            """,
            (thread_id,),
        )
        connection.execute(
            """
            insert into agent_runs (
                id, session_id, graph_name, graph_version, agent_profile_key,
                agent_profile_version, thread_id, status
            )
            values (
                %s, %s, 'runtime_selection_spike', 'runtime-spike-v1',
                'runtime_selection_spike', 1, %s, 'created'
            )
            """,
            (run_id, thread_id, thread_id),
        )


def cleanup_spike_run(database_url: str, *, thread_id: str) -> None:
    with psycopg.connect(database_url, autocommit=True) as connection:
        connection.execute(
            "delete from checkpoint_writes where thread_id = %s",
            (thread_id,),
        )
        connection.execute(
            "delete from checkpoint_blobs where thread_id = %s",
            (thread_id,),
        )
        connection.execute(
            "delete from checkpoints where thread_id = %s",
            (thread_id,),
        )
        connection.execute(
            "delete from agent_sessions where id = %s",
            (thread_id,),
        )


def count_effects(database_url: str, operation_id: str) -> int:
    with psycopg.connect(database_url, autocommit=True) as connection:
        row = connection.execute(
            """
            select count(*)
            from tool_side_effects
            where tool_call_id = %s
            """,
            (operation_id,),
        ).fetchone()
    if not row:
        raise RuntimeError("side-effect count query returned no row")
    return int(row[0])


def resume_and_hang(
    database_url: str,
    *,
    thread_id: str,
    run_id: str,
    kill_point: KillPoint,
    marker_queue: Any,
) -> None:
    ledger = PostgresIdempotencyLedger(
        database_url=database_url,
        run_id=run_id,
        kill_point=kill_point,
        marker_queue=marker_queue,
    )
    with phase0_postgres_checkpointer() as checkpointer:
        graph = build_runtime_selection_spike_graph(
            side_effects=ledger,
            checkpointer=checkpointer,
            control_plane=PostgresRuntimeControlPlane(database_url),
        )
        graph.invoke(Command(resume="approved"), runtime_config(thread_id))


def resume_until_cancelled(
    database_url: str,
    *,
    thread_id: str,
    run_id: str,
    marker_queue: Any,
) -> None:
    from app.infrastructure.graph.runtime_selection_spike import RuntimeSpikeCancelled

    ledger = PostgresIdempotencyLedger(
        database_url=database_url,
        run_id=run_id,
        marker_queue=marker_queue,
        wait_for_cancel=True,
    )
    try:
        with phase0_postgres_checkpointer() as checkpointer:
            graph = build_runtime_selection_spike_graph(
                side_effects=ledger,
                checkpointer=checkpointer,
                control_plane=PostgresRuntimeControlPlane(database_url),
            )
            graph.invoke(Command(resume="approved"), runtime_config(thread_id))
    except RuntimeSpikeCancelled:
        marker_queue.put("cancelled")
        return
    raise AssertionError("running worker did not observe cancellation")


def resume_normally(
    database_url: str,
    *,
    thread_id: str,
    run_id: str,
) -> None:
    with phase0_postgres_checkpointer() as checkpointer:
        graph = build_runtime_selection_spike_graph(
            side_effects=PostgresIdempotencyLedger(database_url, run_id),
            checkpointer=checkpointer,
            control_plane=PostgresRuntimeControlPlane(database_url),
        )
        completed = graph.invoke(Command(resume="approved"), runtime_config(thread_id))
    if completed.get("completed") is not True:
        raise AssertionError("parallel worker did not complete")


def run_kill_case(database_url: str, kill_point: KillPoint) -> dict[str, object]:
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    operation_id = f"{run_id}:side-effect"
    create_spike_run(database_url, thread_id=thread_id, run_id=run_id)
    try:
        with phase0_postgres_checkpointer() as checkpointer:
            graph = build_runtime_selection_spike_graph(
                side_effects=PostgresIdempotencyLedger(database_url, run_id),
                checkpointer=checkpointer,
                control_plane=PostgresRuntimeControlPlane(database_url),
            )
            interrupted = graph.invoke(
                initial_state(thread_id, run_id),
                runtime_config(thread_id),
            )
        if "__interrupt__" not in interrupted:
            raise AssertionError("runtime spike did not reach interrupt")

        context = multiprocessing.get_context("spawn")
        marker_queue = context.Queue()
        worker = context.Process(
            target=resume_and_hang,
            kwargs={
                "database_url": database_url,
                "thread_id": thread_id,
                "run_id": run_id,
                "kill_point": kill_point,
                "marker_queue": marker_queue,
            },
        )
        worker.start()
        try:
            marker = marker_queue.get(timeout=30)
        except queue.Empty as exc:
            worker.kill()
            worker.join(timeout=10)
            raise AssertionError(
                f"worker did not reach injected kill point: {kill_point}"
            ) from exc
        if marker != kill_point:
            raise AssertionError(
                f"unexpected worker marker: expected={kill_point} actual={marker}"
            )

        worker.kill()
        worker.join(timeout=10)
        if worker.is_alive():
            raise AssertionError("SIGKILL worker did not terminate")
        if worker.exitcode is None or worker.exitcode >= 0:
            raise AssertionError(f"worker was not killed by signal: {worker.exitcode}")

        with phase0_postgres_checkpointer() as checkpointer:
            replacement = build_runtime_selection_spike_graph(
                side_effects=PostgresIdempotencyLedger(database_url, run_id),
                checkpointer=checkpointer,
                control_plane=PostgresRuntimeControlPlane(database_url),
            )
            completed = replacement.invoke(None, runtime_config(thread_id))

        effect_count = count_effects(database_url, operation_id)
        if completed.get("completed") is not True:
            raise AssertionError("replacement worker did not complete the run")
        if effect_count != 1:
            raise AssertionError(
                f"side effect was not exactly once: count={effect_count}"
            )
        return {
            "kill_point": kill_point,
            "worker_exitcode": worker.exitcode,
            "replacement_completed": True,
            "durable_side_effect_count": effect_count,
        }
    finally:
        cleanup_spike_run(database_url, thread_id=thread_id)


def run_cancel_case(database_url: str) -> dict[str, object]:
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    operation_id = f"{run_id}:side-effect"
    create_spike_run(database_url, thread_id=thread_id, run_id=run_id)
    control_plane = PostgresRuntimeControlPlane(database_url)
    try:
        with phase0_postgres_checkpointer() as checkpointer:
            graph = build_runtime_selection_spike_graph(
                side_effects=PostgresIdempotencyLedger(database_url, run_id),
                checkpointer=checkpointer,
                control_plane=control_plane,
            )
            graph.invoke(initial_state(thread_id, run_id), runtime_config(thread_id))

        context = multiprocessing.get_context("spawn")
        marker_queue = context.Queue()
        worker = context.Process(
            target=resume_until_cancelled,
            kwargs={
                "database_url": database_url,
                "thread_id": thread_id,
                "run_id": run_id,
                "marker_queue": marker_queue,
            },
        )
        worker.start()
        try:
            running_marker = marker_queue.get(timeout=30)
        except queue.Empty as exc:
            worker.kill()
            worker.join(timeout=10)
            raise AssertionError("worker did not enter cancellable operation") from exc
        if running_marker != "running":
            raise AssertionError(f"unexpected cancel marker: {running_marker}")

        control_plane.request_cancel(run_id)
        try:
            cancelled_marker = marker_queue.get(timeout=30)
        except queue.Empty as exc:
            worker.kill()
            worker.join(timeout=10)
            raise AssertionError("worker did not observe cancellation") from exc
        worker.join(timeout=10)
        if cancelled_marker != "cancelled" or worker.exitcode != 0:
            raise AssertionError(
                f"cancel result mismatch: marker={cancelled_marker} exit={worker.exitcode}"
            )
        effect_count = count_effects(database_url, operation_id)
        if effect_count != 0:
            raise AssertionError("cancelled operation committed a side effect")
        return {
            "running_cancel_observed": True,
            "terminal_status": control_plane.status(run_id),
            "durable_side_effect_count": effect_count,
        }
    finally:
        cleanup_spike_run(database_url, thread_id=thread_id)


def prepare_waiting_run(
    database_url: str,
) -> tuple[str, str]:
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    create_spike_run(database_url, thread_id=thread_id, run_id=run_id)
    with phase0_postgres_checkpointer() as checkpointer:
        graph = build_runtime_selection_spike_graph(
            side_effects=PostgresIdempotencyLedger(database_url, run_id),
            checkpointer=checkpointer,
            control_plane=PostgresRuntimeControlPlane(database_url),
        )
        graph.invoke(initial_state(thread_id, run_id), runtime_config(thread_id))
    return thread_id, run_id


def run_parallel_workers_case(database_url: str) -> dict[str, object]:
    runs = [prepare_waiting_run(database_url) for _ in range(2)]
    context = multiprocessing.get_context("spawn")
    workers = [
        context.Process(
            target=resume_normally,
            kwargs={
                "database_url": database_url,
                "thread_id": thread_id,
                "run_id": run_id,
            },
        )
        for thread_id, run_id in runs
    ]
    try:
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(timeout=30)
        if any(worker.is_alive() for worker in workers):
            for worker in workers:
                if worker.is_alive():
                    worker.kill()
                    worker.join(timeout=10)
            raise AssertionError("parallel runtime workers timed out")
        exitcodes = [worker.exitcode for worker in workers]
        if exitcodes != [0, 0]:
            raise AssertionError(f"parallel workers failed: {exitcodes}")
        statuses = [
            PostgresRuntimeControlPlane(database_url).status(run_id)
            for _, run_id in runs
        ]
        effect_counts = [
            count_effects(database_url, f"{run_id}:side-effect")
            for _, run_id in runs
        ]
        if statuses != ["completed", "completed"] or effect_counts != [1, 1]:
            raise AssertionError(
                f"parallel worker result mismatch: {statuses=} {effect_counts=}"
            )
        return {
            "worker_processes": 2,
            "worker_exitcodes": exitcodes,
            "terminal_statuses": statuses,
            "durable_side_effect_counts": effect_counts,
        }
    finally:
        for thread_id, _ in runs:
            cleanup_spike_run(database_url, thread_id=thread_id)


def run_postgres_outage_case(database_url: str) -> dict[str, object]:
    thread_id = str(uuid.uuid4())
    run_id = str(uuid.uuid4())
    operation_id = f"{run_id}:side-effect"
    create_spike_run(database_url, thread_id=thread_id, run_id=run_id)
    control_plane = PostgresRuntimeControlPlane(database_url)
    try:
        with phase0_postgres_checkpointer() as checkpointer:
            graph = build_runtime_selection_spike_graph(
                side_effects=PostgresIdempotencyLedger(database_url, run_id),
                checkpointer=checkpointer,
                control_plane=control_plane,
            )
            graph.invoke(initial_state(thread_id, run_id), runtime_config(thread_id))

        unavailable_url = (
            "postgresql://runtime:runtime@127.0.0.1:1/runtime"
            "?connect_timeout=1"
        )
        outage_observed = False
        try:
            with PostgresSaver.from_conn_string(unavailable_url) as checkpointer:
                unavailable_graph = build_runtime_selection_spike_graph(
                    side_effects=PostgresIdempotencyLedger(database_url, run_id),
                    checkpointer=checkpointer,
                    control_plane=control_plane,
                )
                unavailable_graph.invoke(
                    Command(resume="approved"),
                    runtime_config(thread_id),
                )
        except psycopg.OperationalError:
            outage_observed = True

        before_recovery_count = count_effects(database_url, operation_id)
        with phase0_postgres_checkpointer() as checkpointer:
            replacement = build_runtime_selection_spike_graph(
                side_effects=PostgresIdempotencyLedger(database_url, run_id),
                checkpointer=checkpointer,
                control_plane=control_plane,
            )
            completed = replacement.invoke(
                Command(resume="approved"),
                runtime_config(thread_id),
            )
        after_recovery_count = count_effects(database_url, operation_id)
        if not outage_observed:
            raise AssertionError("PostgreSQL outage was not observed")
        if before_recovery_count != 0:
            raise AssertionError("PostgreSQL outage allowed a side effect")
        if completed.get("completed") is not True or after_recovery_count != 1:
            raise AssertionError("run did not recover after PostgreSQL returned")
        return {
            "outage_observed": True,
            "fail_closed_side_effect_count": before_recovery_count,
            "replacement_completed": True,
            "recovered_side_effect_count": after_recovery_count,
        }
    finally:
        cleanup_spike_run(database_url, thread_id=thread_id)


def main() -> None:
    database_url = get_psycopg_database_url()
    kill_results = [
        run_kill_case(database_url, "before_commit"),
        run_kill_case(database_url, "after_commit"),
    ]
    cancel_result = run_cancel_case(database_url)
    parallel_result = run_parallel_workers_case(database_url)
    postgres_outage_result = run_postgres_outage_case(database_url)
    print(
        json.dumps(
            {
                "candidate": "A-custom-runtime",
                "checkpoint": "postgres",
                "process_death": "SIGKILL",
                "result": "passed",
                "kill_cases": kill_results,
                "cancel": cancel_result,
                "parallel_workers": parallel_result,
                "postgres_outage": postgres_outage_result,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
