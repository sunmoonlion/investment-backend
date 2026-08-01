# ADR-025: Postgres Checkpoint and Event Persistence

Date: 2026-07-09
Status: Accepted

## Context

M1 needs durable graph checkpoint state and replayable timeline events. The
runtime database account must remain least-privilege and must not create tables
at worker startup.

## Decision

Use LangGraph Postgres checkpointer for runtime state snapshots and use
application-owned tables for agent sessions, runs, domain events, UI events, and
side-effect idempotency records.

Checkpoint tables are created by Alembic migrations, not by
`checkpointer.setup()` in the worker.

## Consequences

- State snapshots are runtime persistence, not domain tables.
- `session_events` remains the durable SSE replay source.
- Runtime workers do not need DDL privileges.
- Checkpoint recovery must be validated across separate DB/checkpointer
  connections.
