# ADR-003: Event, Message, Memory, and Checkpoint Boundaries

Date: 2026-07-08
Status: Accepted

## Context

Agent systems fail when execution state, event history, LLM context, and memory
are treated as one mutable blob.

## Decision

Keep the following meanings separate:

- `StoredMessage`: persisted LLM context message.
- `DomainEvent`: append-only fact that happened.
- `UIEvent`: rebuildable projection for frontend timeline.
- `AgentMemory`: session-scoped recallable context and summaries.
- `LongTermMemory`: cross-session recallable facts or experience, M2-depth.
- `Checkpoint`: persisted runtime state snapshot for one LangGraph thread.

Checkpoint is not memory. Memory is not the event log. The event log is not the
LLM context. UIEvent is not the source of truth.

## Consequences

- There is no `state` table in M1.
- LangGraph checkpointer persists runtime state snapshots.
- `session_events` stores domain/UI event records.
- Memory tables and repositories evolve separately from checkpoint and event
  storage.
- Frontend consumes UIEvent/LiveDelta, not raw LangGraph events.
