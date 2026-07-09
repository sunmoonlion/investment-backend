# ADR-016: HITL Interrupt and Resume

Date: 2026-07-09
Status: Accepted

## Context

M1 must prove human-in-the-loop pause and resume before larger graph work.
Resume must be tied to the same session thread and guarded by stored run state.

## Decision

Use LangGraph interrupt for the runtime pause and persist the domain run as
`waiting` with a resume token. Resume requests must validate the run exists, is
waiting, has a stored token, and receives the matching token before dispatching
the graph task.

The graph thread id is the agent session id.

## Consequences

- Resume cannot bypass domain run validation.
- Waiting UI is projected from `HumanInputRequested`.
- Runtime resume translation stays in infrastructure.
- Real worker kill/restart validation remains a release-gate script, while
  checkpoint recovery is already covered across separate DB connections.
