from __future__ import annotations

import sqlite3

import pytest

from app.infrastructure.graph.execution_identity_spike import (
    AttemptReason,
    IdentityConflict,
    InMemoryExecutionIdentityStore,
    SpikeAttemptStatus,
    SpikeRunStatus,
    create_sqlite_execution_identity_schema,
)


def build_waiting_run():
    store = InMemoryExecutionIdentityStore()
    session = store.create_session(owner_actor_id="actor-1")
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
    attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=0,
        worker_id="worker-a",
        reason=AttemptReason.initial,
    )
    checkpoint = store.mark_waiting(
        attempt_id=attempt.id,
        checkpoint_ns="",
        checkpoint_id="checkpoint-1",
    )
    return store, session, thread, run, attempt, checkpoint


def test_entity_ids_are_distinct_and_never_reused_for_another_entity() -> None:
    store, session, thread, run, attempt, _ = build_waiting_run()
    root = store.invocations[run.root_invocation_id]

    values = {
        session.id.value,
        thread.id.value,
        run.id.value,
        attempt.id.value,
        root.id.value,
    }
    assert len(values) == 5
    assert session.id.value.startswith("session_")
    assert thread.id.value.startswith("thread_")
    assert run.id.value.startswith("run_")
    assert attempt.id.value.startswith("attempt_")
    assert root.id.value.startswith("invocation_")
    assert session.id.value != thread.id.value


def test_one_session_can_own_two_runs_without_reusing_thread_or_run_identity() -> None:
    store, session, thread, run_1, attempt_1, _ = build_waiting_run()
    run_2 = store.create_run(
        session_id=session.id,
        thread_id=thread.id,
        agent_profile_key="root-agent",
    )

    assert run_1.id != run_2.id
    assert run_1.thread_id == run_2.thread_id
    assert run_1.session_id == run_2.session_id

    with pytest.raises(IdentityConflict, match="thread already has active run"):
        store.begin_attempt(
            run_id=run_2.id,
            expected_run_version=0,
            worker_id="worker-b",
            reason=AttemptReason.initial,
        )

    resume = store.begin_attempt(
        run_id=run_1.id,
        expected_run_version=2,
        worker_id="worker-a2",
        reason=AttemptReason.resume,
    )
    store.complete_attempt(attempt_id=resume.id)
    run_2_attempt = store.begin_attempt(
        run_id=run_2.id,
        expected_run_version=0,
        worker_id="worker-b",
        reason=AttemptReason.initial,
    )
    assert run_2_attempt.run_id == run_2.id
    assert attempt_1.id != run_2_attempt.id


def test_resume_resolves_thread_checkpoint_and_creates_new_attempt() -> None:
    store, _, thread, run, first_attempt, checkpoint = build_waiting_run()

    resolved_thread, resolved_checkpoint = store.resolve_resume(run.id)
    resumed_attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=2,
        worker_id="worker-resume",
        reason=AttemptReason.resume,
    )

    assert resolved_thread.id == thread.id
    assert resolved_checkpoint == checkpoint
    assert resumed_attempt.id != first_attempt.id
    assert resumed_attempt.ordinal == 2
    assert resumed_attempt.start_checkpoint_id == "checkpoint-1"
    assert resumed_attempt.reason == AttemptReason.resume


def test_retry_creates_second_execution_attempt_for_same_logical_run() -> None:
    store, _, _, run, _, _ = build_waiting_run()
    resumed_attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=2,
        worker_id="worker-resume",
        reason=AttemptReason.resume,
    )
    store.fail_attempt(attempt_id=resumed_attempt.id, retryable=True)
    retry_attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=4,
        worker_id="worker-retry",
        reason=AttemptReason.retry,
    )

    assert retry_attempt.run_id == run.id
    assert retry_attempt.id != resumed_attempt.id
    assert retry_attempt.ordinal == 3
    assert retry_attempt.reason == AttemptReason.retry
    assert store.runs[run.id].status == SpikeRunStatus.running
    assert store.attempts[resumed_attempt.id].status == SpikeAttemptStatus.failed


def test_subagent_is_child_invocation_not_a_new_run_or_attempt() -> None:
    store, _, _, run, _, _ = build_waiting_run()
    resumed_attempt = store.begin_attempt(
        run_id=run.id,
        expected_run_version=2,
        worker_id="worker-resume",
        reason=AttemptReason.resume,
    )
    child = store.create_child_invocation(
        run_id=run.id,
        attempt_id=resumed_attempt.id,
        parent_invocation_id=run.root_invocation_id,
        agent_profile_key="subagent-literature",
    )
    lineage = store.invocation_lineage(child.id)

    assert [item.id for item in lineage] == [run.root_invocation_id, child.id]
    assert child.run_id == run.id
    assert child.created_attempt_id == resumed_attempt.id
    assert child.root_invocation_id == run.root_invocation_id
    assert len(store.runs) == 1
    assert len(store.attempts) == 2


def test_expected_version_prevents_two_workers_claiming_same_run() -> None:
    store, _, _, run, _, _ = build_waiting_run()
    observed_version = store.runs[run.id].version

    winner = store.begin_attempt(
        run_id=run.id,
        expected_run_version=observed_version,
        worker_id="worker-winner",
        reason=AttemptReason.resume,
    )
    with pytest.raises(IdentityConflict, match="run version changed"):
        store.begin_attempt(
            run_id=run.id,
            expected_run_version=observed_version,
            worker_id="worker-loser",
            reason=AttemptReason.resume,
        )

    assert winner.worker_id == "worker-winner"
    assert store.runs[run.id].version == observed_version + 1


def test_cross_session_thread_and_invocation_links_are_rejected() -> None:
    store, _, _, run, _, _ = build_waiting_run()
    other_session = store.create_session(owner_actor_id="actor-2")
    other_thread = store.create_thread(
        session_id=other_session.id,
        graph_name="research",
        graph_version="v1",
    )

    with pytest.raises(IdentityConflict, match="thread belongs to another session"):
        store.create_run(
            session_id=other_session.id,
            thread_id=store.runs[run.id].thread_id,
            agent_profile_key="invalid",
        )

    other_run = store.create_run(
        session_id=other_session.id,
        thread_id=other_thread.id,
        agent_profile_key="other-root",
    )
    other_attempt = store.begin_attempt(
        run_id=other_run.id,
        expected_run_version=0,
        worker_id="worker-other",
        reason=AttemptReason.initial,
    )
    with pytest.raises(IdentityConflict, match="crosses run boundary"):
        store.create_child_invocation(
            run_id=other_run.id,
            attempt_id=other_attempt.id,
            parent_invocation_id=run.root_invocation_id,
            agent_profile_key="invalid-child",
        )


def test_relational_schema_enforces_identity_and_concurrency_constraints() -> None:
    connection = sqlite3.connect(":memory:")
    create_sqlite_execution_identity_schema(connection)
    connection.execute(
        "insert into execution_sessions (id, owner_actor_id) values (?, ?)",
        ("session_1", "actor-1"),
    )
    connection.execute(
        """
        insert into execution_threads (
            id, session_id, graph_name, graph_version
        ) values (?, ?, ?, ?)
        """,
        ("thread_1", "session_1", "research", "v1"),
    )
    connection.execute(
        """
        insert into execution_runs (
            id, session_id, thread_id, status, version
        ) values (?, ?, ?, 'created', 0)
        """,
        ("run_1", "session_1", "thread_1"),
    )
    connection.execute(
        """
        insert into agent_invocations (
            id, run_id, root_invocation_id, parent_invocation_id,
            created_attempt_id, agent_profile_key, status
        ) values (?, ?, ?, null, null, ?, 'planned')
        """,
        ("invocation_root", "run_1", "invocation_root", "root-agent"),
    )
    connection.execute(
        "update execution_runs set root_invocation_id = ? where id = ?",
        ("invocation_root", "run_1"),
    )

    winner = connection.execute(
        """
        update execution_runs
        set status = 'running', version = version + 1
        where id = ? and version = ? and status = 'created'
        """,
        ("run_1", 0),
    )
    loser = connection.execute(
        """
        update execution_runs
        set status = 'running', version = version + 1
        where id = ? and version = ? and status = 'created'
        """,
        ("run_1", 0),
    )
    assert winner.rowcount == 1
    assert loser.rowcount == 0

    connection.execute(
        """
        insert into run_attempts (
            id, run_id, ordinal, reason, worker_id, status
        ) values (?, ?, 1, 'initial', ?, 'running')
        """,
        ("attempt_1", "run_1", "worker-1"),
    )
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            insert into run_attempts (
                id, run_id, ordinal, reason, worker_id, status
            ) values (?, ?, 2, 'retry', ?, 'running')
            """,
            ("attempt_2", "run_1", "worker-2"),
        )

    connection.execute(
        """
        update run_attempts
        set status = 'waiting', end_checkpoint_id = 'checkpoint-1'
        where id = 'attempt_1'
        """
    )
    connection.execute(
        """
        update execution_runs
        set status = 'waiting', version = version + 1
        where id = 'run_1'
        """
    )
    connection.execute(
        """
        insert into checkpoint_bindings (
            thread_id, checkpoint_ns, checkpoint_id, graph_version
        ) values ('thread_1', '', 'checkpoint-1', 'v1')
        """
    )
    connection.execute(
        """
        insert into run_attempts (
            id, run_id, ordinal, reason, worker_id, status,
            start_checkpoint_id
        ) values (?, ?, 2, 'resume', ?, 'running', 'checkpoint-1')
        """,
        ("attempt_2", "run_1", "worker-2"),
    )
    connection.execute(
        """
        insert into agent_invocations (
            id, run_id, root_invocation_id, parent_invocation_id,
            created_attempt_id, agent_profile_key, status
        ) values (?, ?, ?, ?, ?, ?, 'running')
        """,
        (
            "invocation_child",
            "run_1",
            "invocation_root",
            "invocation_root",
            "attempt_2",
            "subagent",
        ),
    )

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            """
            insert into execution_threads (
                id, session_id, graph_name, graph_version
            ) values ('run_wrong_type', 'session_1', 'research', 'v1')
            """
        )

    checkpoint = connection.execute(
        """
        select checkpoint_id, graph_version
        from checkpoint_bindings
        where thread_id = 'thread_1'
        """
    ).fetchone()
    child = connection.execute(
        """
        select root_invocation_id, parent_invocation_id, created_attempt_id
        from agent_invocations
        where id = 'invocation_child'
        """
    ).fetchone()
    assert checkpoint == ("checkpoint-1", "v1")
    assert child == ("invocation_root", "invocation_root", "attempt_2")
