# ADR-021: Security Context M1 Placeholder

Date: 2026-07-09
Status: Accepted

## Context

M1 is single-tenant, but commands and queued graph tasks must already carry the
security shape needed by later multi-tenant work.

## Decision

Introduce a fixed M1 `SecurityContext` with tenant, actor, roles, permissions,
and schema version. Carry it across create, resume, cancel, Celery dispatch, and
graph task execution.

M1 does not implement tenant isolation, quotas, or real policy evaluation.

## Consequences

- Queue-boundary tests must prove the context is propagated.
- Later M2 security can strengthen validation without changing command shapes.
- Logs and lineage can include security context identifiers when useful.
- No M1 code should treat the placeholder as production authorization.
