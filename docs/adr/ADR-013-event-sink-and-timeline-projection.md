# ADR-013: Event Sink and Timeline Projection

Date: 2026-07-09
Status: Accepted

## Context

The agent runtime emits domain events, while clients need stable timeline
events. Persisted replay and live streaming must use the same semantic source.

## Decision

Use domain events as the write-side semantic record. `TimelineProjector`
converts domain events into `UIEvent` records. `DBEventSink` persists the domain
event and projected UI event in Postgres, then publishes the UI event through
Redis Pub/Sub.

Projection is handler-based so new domain event types do not create branching in
the graph runner.

## Consequences

- `session_events.id` is the SSE replay cursor.
- Clients replay persisted `UIEvent` records before subscribing to live Redis
  channels.
- Domain events remain internal and can evolve behind projection handlers.
- Unknown event types use a conservative fallback projection instead of breaking
  the stream.
