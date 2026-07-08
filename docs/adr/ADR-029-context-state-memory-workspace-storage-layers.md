# ADR-029: Context, State, Memory, Workspace, and Storage Layers

Date: 2026-07-08
Status: Accepted

## Context

The latest v4 plan makes layer separation a first-class gate. The implementation
must avoid turning any one object into a universal container.

## Decision

Use these layer definitions:

- `Context`: temporary input package assembled before an LLM call.
- `State`: graph execution state, persisted only through checkpointer snapshots.
- `Memory`: recallable facts, experience, summaries, or preferences with
  provenance and safety metadata.
- `Workspace/Project`: M2 product organization layer for sessions, files,
  knowledge bindings, default AgentProfile, and permissions.
- `Database`: structured facts, metadata, event stream, permissions, indexes,
  and references.
- `Object Storage`: large objects such as uploads, screenshots, exports, tool
  artifacts, and long logs.

M1 must not create `workspaces` or `projects` entities/tables. `project_id`, if
introduced in M1, is only a placeholder/reference field.

## Consequences

- AgentState must not contain file bodies, full event history, or memory dumps.
- Memory records must carry source/provenance, confidence, scope, and safety
  flags.
- Database rows store object URI/ref/hash/metadata instead of large object
  bodies.
- Context is not persisted as a durable source of truth.
- Tests should include at least one no-mixing assertion for each layer as the
  corresponding implementation appears.
