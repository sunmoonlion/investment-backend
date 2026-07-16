"""Isolated V5-P0-002 execution identity reference model.

This module is not imported by API routes or workers. It freezes identity,
state, checkpoint and lineage semantics before production tables are designed.
"""

from __future__ import annotations

import uuid
import sqlite3
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


class IdentityConflict(RuntimeError):
    pass


def create_sqlite_execution_identity_schema(
    connection: sqlite3.Connection,
) -> None:
    schema_path = Path(__file__).with_suffix(".sql")
    connection.executescript(schema_path.read_text(encoding="utf-8"))


@dataclass(frozen=True)
class SessionId:
    value: str


@dataclass(frozen=True)
class ThreadId:
    value: str


@dataclass(frozen=True)
class RunId:
    value: str


@dataclass(frozen=True)
class AttemptId:
    value: str


@dataclass(frozen=True)
class InvocationId:
    value: str


class SpikeRunStatus(StrEnum):
    created = "created"
    running = "running"
    waiting = "waiting"
    retry_pending = "retry_pending"
    completed = "completed"
    failed = "failed"
    cancelled = "cancelled"


class SpikeAttemptStatus(StrEnum):
    running = "running"
    waiting = "waiting"
    succeeded = "succeeded"
    failed = "failed"


class SpikeInvocationStatus(StrEnum):
    planned = "planned"
    running = "running"
    waiting = "waiting"
    completed = "completed"
    failed = "failed"


class AttemptReason(StrEnum):
    initial = "initial"
    resume = "resume"
    retry = "retry"


@dataclass
class ExecutionSession:
    id: SessionId
    owner_actor_id: str


@dataclass
class ExecutionThread:
    id: ThreadId
    session_id: SessionId
    graph_name: str
    graph_version: str
    parent_thread_id: ThreadId | None = None


@dataclass
class ExecutionRun:
    id: RunId
    session_id: SessionId
    thread_id: ThreadId
    root_invocation_id: InvocationId
    status: SpikeRunStatus = SpikeRunStatus.created
    version: int = 0


@dataclass
class RunAttempt:
    id: AttemptId
    run_id: RunId
    ordinal: int
    reason: AttemptReason
    worker_id: str
    status: SpikeAttemptStatus = SpikeAttemptStatus.running
    start_checkpoint_id: str | None = None
    end_checkpoint_id: str | None = None


@dataclass
class AgentInvocation:
    id: InvocationId
    run_id: RunId
    root_invocation_id: InvocationId
    parent_invocation_id: InvocationId | None
    created_attempt_id: AttemptId | None
    agent_profile_key: str
    status: SpikeInvocationStatus = SpikeInvocationStatus.planned


@dataclass(frozen=True)
class CheckpointBinding:
    thread_id: ThreadId
    checkpoint_ns: str
    checkpoint_id: str
    graph_version: str


@dataclass
class InMemoryExecutionIdentityStore:
    sessions: dict[SessionId, ExecutionSession] = field(default_factory=dict)
    threads: dict[ThreadId, ExecutionThread] = field(default_factory=dict)
    runs: dict[RunId, ExecutionRun] = field(default_factory=dict)
    attempts: dict[AttemptId, RunAttempt] = field(default_factory=dict)
    invocations: dict[InvocationId, AgentInvocation] = field(default_factory=dict)
    checkpoints: dict[ThreadId, CheckpointBinding] = field(default_factory=dict)

    def create_session(self, *, owner_actor_id: str) -> ExecutionSession:
        session = ExecutionSession(
            id=self._new_id(SessionId, "session"),
            owner_actor_id=owner_actor_id,
        )
        self.sessions[session.id] = session
        return session

    def create_thread(
        self,
        *,
        session_id: SessionId,
        graph_name: str,
        graph_version: str,
        parent_thread_id: ThreadId | None = None,
    ) -> ExecutionThread:
        self._session(session_id)
        if parent_thread_id:
            parent = self._thread(parent_thread_id)
            if parent.session_id != session_id:
                raise IdentityConflict("parent thread belongs to another session")
        thread = ExecutionThread(
            id=self._new_id(ThreadId, "thread"),
            session_id=session_id,
            graph_name=graph_name,
            graph_version=graph_version,
            parent_thread_id=parent_thread_id,
        )
        self.threads[thread.id] = thread
        return thread

    def create_run(
        self,
        *,
        session_id: SessionId,
        thread_id: ThreadId,
        agent_profile_key: str,
    ) -> ExecutionRun:
        self._session(session_id)
        thread = self._thread(thread_id)
        if thread.session_id != session_id:
            raise IdentityConflict("thread belongs to another session")
        run_id = self._new_id(RunId, "run")
        root_invocation_id = self._new_id(InvocationId, "invocation")
        run = ExecutionRun(
            id=run_id,
            session_id=session_id,
            thread_id=thread_id,
            root_invocation_id=root_invocation_id,
        )
        root_invocation = AgentInvocation(
            id=root_invocation_id,
            run_id=run_id,
            root_invocation_id=root_invocation_id,
            parent_invocation_id=None,
            created_attempt_id=None,
            agent_profile_key=agent_profile_key,
        )
        self.runs[run.id] = run
        self.invocations[root_invocation.id] = root_invocation
        return run

    def begin_attempt(
        self,
        *,
        run_id: RunId,
        expected_run_version: int,
        worker_id: str,
        reason: AttemptReason,
    ) -> RunAttempt:
        run = self._run(run_id)
        if run.version != expected_run_version:
            raise IdentityConflict(
                f"run version changed: expected={expected_run_version} actual={run.version}"
            )
        if self._running_attempt(run_id):
            raise IdentityConflict("run already has a running attempt")
        active_run = self._active_run_for_thread(run.thread_id, excluding=run_id)
        if active_run:
            raise IdentityConflict(
                f"thread already has active run: {active_run.id.value}"
            )
        self._validate_attempt_reason(run, reason)

        ordinal = 1 + max(
            (
                attempt.ordinal
                for attempt in self.attempts.values()
                if attempt.run_id == run_id
            ),
            default=0,
        )
        checkpoint = self.checkpoints.get(run.thread_id)
        attempt = RunAttempt(
            id=self._new_id(AttemptId, "attempt"),
            run_id=run_id,
            ordinal=ordinal,
            reason=reason,
            worker_id=worker_id,
            start_checkpoint_id=checkpoint.checkpoint_id if checkpoint else None,
        )
        self.attempts[attempt.id] = attempt
        run.status = SpikeRunStatus.running
        run.version += 1
        self.invocations[run.root_invocation_id].status = (
            SpikeInvocationStatus.running
        )
        return attempt

    def mark_waiting(
        self,
        *,
        attempt_id: AttemptId,
        checkpoint_ns: str,
        checkpoint_id: str,
    ) -> CheckpointBinding:
        attempt = self._attempt(attempt_id)
        if attempt.status != SpikeAttemptStatus.running:
            raise IdentityConflict("only a running attempt can enter waiting")
        run = self._run(attempt.run_id)
        thread = self._thread(run.thread_id)
        binding = CheckpointBinding(
            thread_id=thread.id,
            checkpoint_ns=checkpoint_ns,
            checkpoint_id=checkpoint_id,
            graph_version=thread.graph_version,
        )
        self.checkpoints[thread.id] = binding
        attempt.status = SpikeAttemptStatus.waiting
        attempt.end_checkpoint_id = checkpoint_id
        run.status = SpikeRunStatus.waiting
        run.version += 1
        self.invocations[run.root_invocation_id].status = (
            SpikeInvocationStatus.waiting
        )
        return binding

    def fail_attempt(
        self,
        *,
        attempt_id: AttemptId,
        retryable: bool,
    ) -> None:
        attempt = self._attempt(attempt_id)
        if attempt.status != SpikeAttemptStatus.running:
            raise IdentityConflict("only a running attempt can fail")
        run = self._run(attempt.run_id)
        attempt.status = SpikeAttemptStatus.failed
        run.status = (
            SpikeRunStatus.retry_pending if retryable else SpikeRunStatus.failed
        )
        run.version += 1
        self.invocations[run.root_invocation_id].status = (
            SpikeInvocationStatus.running
            if retryable
            else SpikeInvocationStatus.failed
        )

    def complete_attempt(self, *, attempt_id: AttemptId) -> None:
        attempt = self._attempt(attempt_id)
        if attempt.status != SpikeAttemptStatus.running:
            raise IdentityConflict("only a running attempt can complete")
        run = self._run(attempt.run_id)
        attempt.status = SpikeAttemptStatus.succeeded
        run.status = SpikeRunStatus.completed
        run.version += 1
        self.invocations[run.root_invocation_id].status = (
            SpikeInvocationStatus.completed
        )

    def create_child_invocation(
        self,
        *,
        run_id: RunId,
        attempt_id: AttemptId,
        parent_invocation_id: InvocationId,
        agent_profile_key: str,
    ) -> AgentInvocation:
        run = self._run(run_id)
        attempt = self._attempt(attempt_id)
        parent = self._invocation(parent_invocation_id)
        if attempt.run_id != run_id or parent.run_id != run_id:
            raise IdentityConflict("invocation lineage crosses run boundary")
        if attempt.status != SpikeAttemptStatus.running:
            raise IdentityConflict("child invocation requires a running attempt")
        invocation = AgentInvocation(
            id=self._new_id(InvocationId, "invocation"),
            run_id=run_id,
            root_invocation_id=run.root_invocation_id,
            parent_invocation_id=parent.id,
            created_attempt_id=attempt_id,
            agent_profile_key=agent_profile_key,
            status=SpikeInvocationStatus.running,
        )
        self.invocations[invocation.id] = invocation
        return invocation

    def invocation_lineage(
        self,
        invocation_id: InvocationId,
    ) -> list[AgentInvocation]:
        lineage: list[AgentInvocation] = []
        current = self._invocation(invocation_id)
        while True:
            lineage.append(current)
            if current.parent_invocation_id is None:
                break
            current = self._invocation(current.parent_invocation_id)
        lineage.reverse()
        return lineage

    def resolve_resume(
        self,
        run_id: RunId,
    ) -> tuple[ExecutionThread, CheckpointBinding]:
        run = self._run(run_id)
        if run.status != SpikeRunStatus.waiting:
            raise IdentityConflict("run is not waiting")
        try:
            checkpoint = self.checkpoints[run.thread_id]
        except KeyError as exc:
            raise IdentityConflict("waiting run has no checkpoint") from exc
        return self._thread(run.thread_id), checkpoint

    def _validate_attempt_reason(
        self,
        run: ExecutionRun,
        reason: AttemptReason,
    ) -> None:
        expected_status = {
            AttemptReason.initial: SpikeRunStatus.created,
            AttemptReason.resume: SpikeRunStatus.waiting,
            AttemptReason.retry: SpikeRunStatus.retry_pending,
        }[reason]
        if run.status != expected_status:
            raise IdentityConflict(
                f"{reason} attempt requires {expected_status}, got {run.status}"
            )
        if reason == AttemptReason.resume and run.thread_id not in self.checkpoints:
            raise IdentityConflict("resume requires a persisted checkpoint")

    def _active_run_for_thread(
        self,
        thread_id: ThreadId,
        *,
        excluding: RunId,
    ) -> ExecutionRun | None:
        active = {
            SpikeRunStatus.running,
            SpikeRunStatus.waiting,
            SpikeRunStatus.retry_pending,
        }
        return next(
            (
                run
                for run in self.runs.values()
                if run.id != excluding
                and run.thread_id == thread_id
                and run.status in active
            ),
            None,
        )

    def _running_attempt(self, run_id: RunId) -> RunAttempt | None:
        return next(
            (
                attempt
                for attempt in self.attempts.values()
                if attempt.run_id == run_id
                and attempt.status == SpikeAttemptStatus.running
            ),
            None,
        )

    def _session(self, session_id: SessionId) -> ExecutionSession:
        try:
            return self.sessions[session_id]
        except KeyError as exc:
            raise IdentityConflict(f"unknown session: {session_id.value}") from exc

    def _thread(self, thread_id: ThreadId) -> ExecutionThread:
        try:
            return self.threads[thread_id]
        except KeyError as exc:
            raise IdentityConflict(f"unknown thread: {thread_id.value}") from exc

    def _run(self, run_id: RunId) -> ExecutionRun:
        try:
            return self.runs[run_id]
        except KeyError as exc:
            raise IdentityConflict(f"unknown run: {run_id.value}") from exc

    def _attempt(self, attempt_id: AttemptId) -> RunAttempt:
        try:
            return self.attempts[attempt_id]
        except KeyError as exc:
            raise IdentityConflict(f"unknown attempt: {attempt_id.value}") from exc

    def _invocation(self, invocation_id: InvocationId) -> AgentInvocation:
        try:
            return self.invocations[invocation_id]
        except KeyError as exc:
            raise IdentityConflict(
                f"unknown invocation: {invocation_id.value}"
            ) from exc

    @staticmethod
    def _new_id(id_type: type, prefix: str):
        return id_type(f"{prefix}_{uuid.uuid4()}")
