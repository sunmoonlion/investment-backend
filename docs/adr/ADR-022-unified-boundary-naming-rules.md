# ADR-022: Unified Boundary Naming Rules

Date: 2026-07-08
Status: Accepted

## Context

The v4 plan uses several similar-looking concepts: Event, Command, Message,
Memory, Checkpoint, and Transport. Their names must encode semantics, not
implementation convenience.

## Decision

Adopt these naming rules:

- `Command`: request intent that may be rejected.
- `DomainEvent`: fact that already happened and belongs to the audit truth.
- `UIEvent`: projection for user-facing timeline, rebuildable from facts.
- `StoredMessage`: persisted LLM context message.
- `Memory`: recallable experience, facts, summaries, or preferences.
- `Checkpoint`: runtime state snapshot, not memory.
- `TransportMessage`: communication envelope at broker/SSE boundaries, M2.

Events use past-tense/fact names. Commands use request-intent names. Transport
envelopes do not enter domain state, memory, or event payloads.

## Consequences

- `CreateRunCommand`, `ResumeRunCommand`, and `CancelRunCommand` are commands.
- `HumanInputRequested`, `ToolCallCompleted`, and `RunCompleted` are facts.
- `TimelineWaitInputDisplayed` and related timeline entries are UI projections.
- M1 does not introduce a full `TransportMessage` schema.
