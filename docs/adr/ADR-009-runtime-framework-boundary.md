# ADR-009: Runtime Framework Boundary

Date: 2026-07-09
Status: Accepted

## Context

MoocManus v4 uses LangGraph for graph execution, checkpoint, interrupt, and
resume. The domain model must remain usable without importing LangGraph or
LangChain runtime types.

## Decision

Keep LangGraph behind infrastructure adapters. Domain and application services
use ports, commands, domain events, stored messages, and runtime facades.

LangGraph state is a runtime state object, not the domain model. It may contain
only replayable execution data that belongs in the checkpointer snapshot.

## Consequences

- Domain agent code must not import LangGraph or LangChain.
- Application agent code must not import LangGraph directly.
- Resume is expressed by application commands and translated to LangGraph
  `Command(resume=...)` only in the infrastructure adapter.
- Graph-specific state may exist, but shared runtime code must not be named
  after the first concrete graph shape.
