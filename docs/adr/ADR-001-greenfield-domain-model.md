# ADR-001: Greenfield Domain Model

Date: 2026-07-08
Status: Accepted

## Context

MoocManus v4 is rebuilt inside `research-admin-backend`. The old MoocManus
project contains useful domain ideas, but its code shape mixes planner loops,
messages, memory, events, and execution state too tightly.

## Decision

Build a new domain model for Research App agents. Reuse old MoocManus only as a
read-only behavior reference and golden-sample source.

The new domain model owns:

- sessions and runs
- user input and stored messages
- commands and domain events
- UI projections
- memory and policy models
- plan and step models

LangGraph state is not the domain model. It is the execution runtime state and
must stay small, replayable, and checkpointable.

## Consequences

- No old MoocManus code is imported, deployed, or used as fallback.
- Domain models live under `app/domain`.
- Runtime/adapters translate between domain objects and LangGraph/LangChain.
- Old behavior can become golden cases after it is normalized into the new
  vocabulary.
