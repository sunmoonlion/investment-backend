# ADR-015: Replay Idempotency and Side Effects

Date: 2026-07-09
Status: Accepted

## Context

LangGraph checkpoint replay can re-enter nodes after process restart or resume.
Any external side effect must be protected from duplicate execution.

## Decision

Treat graph node execution as replayable. External side effects go through
application services that own idempotency keys. Tool side effects use
`tool_call_id` as the first M1 idempotency key.

Reducers must reject stale state versions and append replayable output only when
it is safe to do so.

## Consequences

- Graph nodes must not write directly to external systems.
- Replaying a checkpoint must not duplicate stored side-effect rows.
- Tests must include a forced replay case for side-effect idempotency.
- More side-effect categories may add their own idempotency keys later.
