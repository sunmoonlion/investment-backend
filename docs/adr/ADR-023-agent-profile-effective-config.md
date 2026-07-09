# ADR-023: Agent Profile and Effective Config

Date: 2026-07-09
Status: Accepted

## Context

The platform needs switchable agent behavior, but M1 must not introduce
multi-agent orchestration or registry rollout machinery.

## Decision

Use lightweight `AgentProfile` and `EffectiveAgentConfig` for M1. A run resolves
the requested profile key/version before persistence and stores the selected
profile identity.

Profiles can vary prompt, tools, model settings, memory policy, and sandbox
permissions inside one graph runtime.

## Consequences

- M1 supports behavior switching without forking graph code.
- AgentRegistry, GraphRegistry, Supervisor, and handoff are M2 concepts.
- Profile resolution must be deterministic and testable offline.
- Existing built-in profiles are enough until rollout/version governance is
  required.
