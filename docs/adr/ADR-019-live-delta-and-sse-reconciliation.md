# ADR-019: Live Delta and SSE Reconciliation

Date: 2026-07-09
Status: Accepted

## Context

Clients need fast live updates while the durable timeline still comes from
persisted UI events. Live-only messages must not become a second source of truth.

## Decision

Use `LiveDelta` for non-persisted live updates. Publish deltas on a separate
Redis channel and include `final_event_id` when a persisted UI event supersedes
or finalizes the delta.

The SSE endpoint replays persisted UI events first, then subscribes to both the
UI event and live-delta channels.

## Consequences

- Persisted `UIEvent` remains the durable timeline record.
- Clients can reconcile temporary live updates with final persisted events.
- Reconnect uses `last_event_id` against persisted events, not live deltas.
- Dropped live deltas are acceptable when the final persisted UI event arrives.
