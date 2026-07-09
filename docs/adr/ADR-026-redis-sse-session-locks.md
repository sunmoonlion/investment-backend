# ADR-026: Redis SSE and Session Locks

Date: 2026-07-09
Status: Accepted

## Context

Redis is already part of the app platform and is needed for live SSE fan-out and
to prevent concurrent graph execution for the same session.

## Decision

Use Redis Pub/Sub for live `UIEvent` and `LiveDelta` delivery. Use a Redis
session lock keyed by `agent:session:{session_id}:lock` before executing a graph
task.

The lock uses `SET ... NX EX`, owner-token checks, TTL renewal, and token-guarded
release. Because the runtime Redis ACL currently disallows `EVAL`, release and
renewal use supported `GET`, `EXPIRE`, and `DEL` commands.

## Consequences

- Postgres remains the source for replay; Redis is live transport and lock
  coordination.
- Lock-busy runs fail with structured `session_locked` errors.
- Workers renew the lock during graph execution.
- Redis ACL provisioning must include the commands required by M1.
