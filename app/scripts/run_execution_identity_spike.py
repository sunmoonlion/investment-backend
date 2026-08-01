from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.infrastructure.graph.execution_identity_spike import (
    AttemptReason,
    InMemoryExecutionIdentityStore,
    create_sqlite_execution_identity_schema,
)


def main() -> None:
    store = InMemoryExecutionIdentityStore()
    session = store.create_session(owner_actor_id="actor-spike")
    thread = store.create_thread(
        session_id=session.id,
        graph_name="research",
        graph_version="v1",
    )
    run = store.create_run(
        session_id=session.id,
        thread_id=thread.id,
        agent_profile_key="root-agent",
    )
    first_attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=0,
        worker_id="worker-1",
        reason=AttemptReason.initial,
    )
    store.mark_waiting(
        attempt_id=first_attempt.id,
        checkpoint_ns="",
        checkpoint_id="checkpoint-1",
    )
    resumed_attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=2,
        worker_id="worker-2",
        reason=AttemptReason.resume,
    )
    store.fail_attempt(attempt_id=resumed_attempt.id, retryable=True)
    retry_attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=4,
        worker_id="worker-3",
        reason=AttemptReason.retry,
    )
    child = store.create_child_invocation(
        run_id=run.id,
        attempt_id=retry_attempt.id,
        parent_invocation_id=run.root_invocation_id,
        agent_profile_key="subagent-literature",
    )
    store.complete_attempt(attempt_id=retry_attempt.id)

    relational = sqlite3.connect(":memory:")
    create_sqlite_execution_identity_schema(relational)
    relational.execute(
        "insert into execution_sessions (id, owner_actor_id) values (?, ?)",
        (session.id.value, session.owner_actor_id),
    )
    relational.execute(
        """
        insert into execution_threads (
            id, session_id, graph_name, graph_version
        ) values (?, ?, ?, ?)
        """,
        (
            thread.id.value,
            session.id.value,
            thread.graph_name,
            thread.graph_version,
        ),
    )
    relational.execute(
        """
        insert into execution_runs (
            id, session_id, thread_id, status, version
        ) values (?, ?, ?, ?, ?)
        """,
        (
            run.id.value,
            session.id.value,
            thread.id.value,
            store.runs[run.id].status.value,
            store.runs[run.id].version,
        ),
    )
    for attempt in sorted(store.attempts.values(), key=lambda item: item.ordinal):
        relational.execute(
            """
            insert into run_attempts (
                id, run_id, ordinal, reason, worker_id, status,
                start_checkpoint_id, end_checkpoint_id
            ) values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                attempt.id.value,
                attempt.run_id.value,
                attempt.ordinal,
                attempt.reason.value,
                attempt.worker_id,
                attempt.status.value,
                attempt.start_checkpoint_id,
                attempt.end_checkpoint_id,
            ),
        )
    for invocation in sorted(
        store.invocations.values(),
        key=lambda item: item.parent_invocation_id is not None,
    ):
        relational.execute(
            """
            insert into agent_invocations (
                id, run_id, root_invocation_id, parent_invocation_id,
                created_attempt_id, agent_profile_key, status
            ) values (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invocation.id.value,
                invocation.run_id.value,
                invocation.root_invocation_id.value,
                (
                    invocation.parent_invocation_id.value
                    if invocation.parent_invocation_id
                    else None
                ),
                (
                    invocation.created_attempt_id.value
                    if invocation.created_attempt_id
                    else None
                ),
                invocation.agent_profile_key,
                invocation.status.value,
            ),
        )
    relational.execute(
        "update execution_runs set root_invocation_id = ? where id = ?",
        (run.root_invocation_id.value, run.id.value),
    )
    checkpoint = store.checkpoints[thread.id]
    relational.execute(
        """
        insert into checkpoint_bindings (
            thread_id, checkpoint_ns, checkpoint_id, graph_version
        ) values (?, ?, ?, ?)
        """,
        (
            checkpoint.thread_id.value,
            checkpoint.checkpoint_ns,
            checkpoint.checkpoint_id,
            checkpoint.graph_version,
        ),
    )
    relational.commit()

    result = {
        "task": "V5-P0-002",
        "result": "passed",
        "session_id": session.id.value,
        "thread_id": thread.id.value,
        "run_id": run.id.value,
        "attempt_ordinals": sorted(
            attempt.ordinal for attempt in store.attempts.values()
        ),
        "attempt_reasons": [
            attempt.reason.value
            for attempt in sorted(
                store.attempts.values(),
                key=lambda item: item.ordinal,
            )
        ],
        "root_invocation_id": run.root_invocation_id.value,
        "child_invocation_id": child.id.value,
        "child_lineage": [
            invocation.id.value
            for invocation in store.invocation_lineage(child.id)
        ],
        "checkpoint_id": store.checkpoints[thread.id].checkpoint_id,
        "run_status": store.runs[run.id].status.value,
        "relational_foreign_keys": bool(
            relational.execute("pragma foreign_keys").fetchone()[0]
        ),
        "relational_attempt_count": relational.execute(
            "select count(*) from run_attempts"
        ).fetchone()[0],
        "relational_invocation_count": relational.execute(
            "select count(*) from agent_invocations"
        ).fetchone()[0],
    }
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    if result["attempt_ordinals"] != [1, 2, 3]:
        raise SystemExit(1)
    if result["attempt_reasons"] != ["initial", "resume", "retry"]:
        raise SystemExit(1)
    if result["run_status"] != "completed":
        raise SystemExit(1)
    if len(result["child_lineage"]) != 2:
        raise SystemExit(1)
    if result["relational_attempt_count"] != 3:
        raise SystemExit(1)
    if result["relational_invocation_count"] != 2:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
