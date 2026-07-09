# ADR-024: M1 and M2 Scope Gates

Date: 2026-07-09
Status: Accepted

## Context

The v4 plan contains both immediate M1 requirements and larger M2 platform
capabilities. Mixing them would make the first release hard to validate.

## Decision

M1 builds one recoverable, streamable, measurable graph product slice. M2 starts
only after the M1 release gate passes.

M1 includes checkpoint/resume, persisted events, SSE replay, LiveDelta
reconciliation, idempotency, lightweight profiles, M1 memory interface, and
deployment closure. M2 owns GraphRegistry, multi-agent orchestration, model
gateway, long-term memory governance, real multi-tenant security, and larger
RAG/evidence assembly.

## Consequences

- Each task must say whether it is M1 or deferred M2.
- User traffic waits for the M1 release gate.
- M2-only modules must not be introduced early as empty architecture.
- The task file remains the source of truth for current gate status.
