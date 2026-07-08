# ADR-030: Strategy, Visitor, and Handler Registry

Date: 2026-07-08
Status: Accepted

## Context

The v4 implementation will grow new tools, projections, memory policies, model
providers, and sandbox modes. Large `if/elif` blocks in graph runners or event
sinks would make the runtime brittle.

## Decision

Use small registries and handlers at real variation points.

M1 variation points:

- `ToolResultHandlerRegistry`
- `TimelineProjector` event-type handlers
- `MemoryPolicy`
- `AgentProfile` / `EffectiveAgentConfig`
- `LLMPort`
- `SandboxPort`

M2 variation points:

- `EvidenceAssembler` sub-strategies
- multi-provider fallback/routing
- AgentRegistry/GraphRegistry rollout

Prefer Python `Protocol`, registry dictionaries, small handler objects, and
clear fallback handlers. Avoid inheritance-heavy patterns unless they solve a
specific local problem.

## Consequences

- graph runners do not branch by `tool_name` or `event_type`.
- New tools add handlers instead of modifying runner main flow.
- New UI projections add projector handlers instead of modifying EventSink.
- New memory behavior adds MemoryPolicy instead of mutating AgentMemory into a
  strategy holder.
- Registries must produce structured errors for unknown types.
