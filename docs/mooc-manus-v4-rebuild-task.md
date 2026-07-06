# MoocManus v4 Rebuild Task

Status: draft
Owner: research-admin-backend
Source plan: `/home/zym/k8s/sunmoonai/docs/mooc-manus-langgraph-longterm-plan-v4.md`
Target codebase: `/home/zym/research-app/research-admin-backend/app`

## 0. Decision

MoocManus v4 is a greenfield rebuild inside `research-admin-backend`.

Keep the platform shell:

- FastAPI entrypoint and router registration.
- Celery app and producer/worker wiring.
- Postgres, Redis, config, logging, exception handling.
- Docker, pyproject, Alembic, bootstrap scripts.
- Existing auth/session-cookie foundation unless it conflicts with agent product semantics.

Do not reuse old MoocManus as the engineering base:

- Do not copy the old hand-written Planner-ReAct loop.
- Do not keep old dict-based message/memory structures.
- Do not keep old overloaded Event semantics.
- Do not store session events/files/memory as one expanding JSON aggregate.
- Use old `/home/zym/imooc-mas/mooc-manus` only as read-only behavior reference and golden sample source.

Strict v4 rule: validation comes before platform buildout. Walking Skeleton is Phase 0 and must run before semantic freeze and full domain modeling.

## 1. V1 Target

Build one working vertical slice:

```text
FastAPI request
  -> create run and enqueue command
  -> Celery worker executes minimal LangGraph
  -> checkpoint/interrupt/resume work with thread_id=session_id
  -> graph emits DomainEvent
  -> Projector creates UIEvent
  -> Redis/SSE streams UIEvent and LiveDelta
  -> last_event_id reconnect补发 works
```

The first graph must be deliberately tiny. It should validate checkpoint, SSE reconciliation, and side-effect idempotency before the larger Planner-ReAct implementation begins.

## 2. V1/V2 Scope Discipline

V1 includes only what is needed for a single graph to run, recover, stream, and be evaluated.

V1 must include:

- Walking Skeleton.
- Minimal golden/eval harness.
- UserInput / StoredMessage / minimal Command naming.
- DomainEvent / UIEvent / RunLineage.
- EventSink + TimelineProjector.
- Celery graph-runner and Redis/SSE path.
- Run state machine, idempotency_key, session lock.
- Reducer and tool side-effect idempotency.
- Checkpoint + interrupt + resume.
- ACL boundary rules.

V1 explicitly does not include:

- TransportMessage envelope.
- Full four event categories.
- IntegrationEvent and RabbitMQ.
- GraphRegistry and rollout/version pinning.
- CQRS materialized read models.
- Multi-provider Model Gateway.
- Supervisor/multi-agent.
- Deep RAGFlow integration.
- Full multi-tenant SecurityContext propagation.
- EvidenceAssembler.

## 3. Phase -1: App Platform Boundary

- [x] Confirm target source is `/home/zym/research-app/research-admin-backend/app`.
- [x] Confirm deployment templates live under `/home/zym/k8s/sunmoonai/app-platform/research-app`.
- [x] Confirm old source is reference only:
  - `/home/zym/imooc-mas/mooc-manus`
  - `/home/zym/imooc/imooc-mas/mooc-manus`
- [x] Confirm Python agent runtime belongs to `research-admin-backend` and Celery worker, not Node BullMQ.
- [ ] Keep this boundary documented in ADR-027.

Acceptance:

- No old project is deployed, imported, or used as fallback.
- New code lands only in the target source tree.

## 4. Phase 0: Walking Skeleton

Source: v4 §6.5 and §35 task 0.

Purpose: validate the three physical assumptions before building the full platform.

Must validate:

- [ ] Checkpoint + interrupt + resume work in the local Celery + Redis + Postgres stack.
- [ ] Worker restart can resume by `thread_id=session_id` without starting over.
- [ ] SSE disconnect + `last_event_id` replay does not miss or duplicate timeline events.
- [ ] Tool side effect idempotency prevents duplicate writes on replay.

Minimal graph:

```text
START
  -> ask_user_node
  -> side_effect_tool_node
  -> END
```

Minimum implementation checklist:

- [ ] Add `langgraph` dependency.
- [ ] Add `langchain-core` dependency.
- [ ] Choose/check V1 Postgres checkpointer dependency.
- [ ] Add minimal run/session/event tables needed by the skeleton.
- [ ] Add a minimal Celery task that runs the graph.
- [ ] Add ask-user interrupt path.
- [ ] Add resume API/command path.
- [ ] Add side-effect cache keyed by `tool_call_id`.
- [ ] Add Redis/SSE stream and `last_event_id`补发 path.
- [ ] Add a validation script for the five §6.5.3 acceptance checks.

Acceptance:

```text
1. Trigger graph -> ask_user -> interrupt -> session.status=waiting.
2. Kill/restart worker -> resume continues from checkpoint.
3. User input resumes graph -> tool node executes -> side effect happens once.
4. Forced replay after tool node -> side effect is not repeated.
5. SSE reconnect with last_event_id -> timeline complete, no missing/duplicate events.
```

## 5. Phase 0.5: Minimal Evaluation Harness

Source: v4 §28.3 and §35 task 0.5.

- [ ] Turn Phase 0 validation script into the first golden case.
- [ ] Add fixture format for golden tasks.
- [ ] Add LLM recording/replay harness placeholder, even if Phase 0 graph uses no LLM.
- [ ] Add CI/test command that can run the golden case deterministically.

Acceptance:

- A future prompt/graph/tool change can be checked against at least one golden case.
- No live LLM call is required for this first validation.

## 6. Phase 1: Semantic Freeze and Message Boundary

Source: v4 §9.2, §10, §11, §31.1.

- [ ] Define `UserInput`.
- [ ] Define `StoredMessage`.
- [ ] Define minimal `CreateRunCommand`, `ResumeRunCommand`, `CancelRunCommand`.
- [ ] Define serializer/upcaster skeleton.
- [ ] Use LangChain Core `BaseMessage` only behind adapters/ACL.
- [ ] Add tests for message sequence validity.
- [ ] Add ADR-001, ADR-002, ADR-003, ADR-022.

Hard rules:

- No `Message = UserInput` legacy alias.
- `Command` names request intent.
- `Event` names facts that already happened.
- Domain models must not import LangGraph.

## 7. Phase 2: EventSink and Projection

Source: v4 §10.4, §18, §24.

- [ ] Define `RunLineage`.
- [ ] Define V1 `DomainEvent`.
- [ ] Define V1 `UIEvent`.
- [ ] Define `EventSink`.
- [ ] Define `TimelineProjector`.
- [ ] Persist `session_events` with `category`, `payload_schema_version`, `lineage`, `payload`, `metadata`.
- [ ] Add LiveDelta/final UIEvent reconciliation path.
- [ ] Add ADR-013 and ADR-019.

Hard rules:

- DomainEvent is append-only truth.
- UIEvent is a rebuildable projection.
- Frontend consumes UIEvent or LiveDelta, never raw LangGraph events.
- Graph control flow must not be driven by events.

## 8. Phase 3: Execution Topology

Source: v4 §6, §15.3, §31.1.

- [ ] API creates run and returns `run_id` immediately.
- [ ] API enqueues graph-runner Celery task.
- [ ] Worker owns graph execution.
- [ ] Add run state machine:
  `created -> running -> waiting <-> running -> completed/failed/cancelled/budget_exceeded`.
- [ ] Add `idempotency_key` handling.
- [ ] Add Redis session lock with TTL/renewal.
- [ ] Add Redis Pub/Sub for UIEvent/LiveDelta SSE.

Acceptance:

- No long agent run executes inside HTTP request lifecycle.
- Duplicate API submit does not create duplicate active runs.

## 9. Phase 4: AgentMemory

Source: v4 §12.

- [ ] Define `AgentMemory`.
- [ ] Define `MemoryPolicy`.
- [ ] Add session-scoped memory repository.
- [ ] Add compaction/summary service.

V1 only:

- Session memory and summarization.
- LongTermMemory interface placeholder only.

## 10. Phase 5: Planner-ReAct Graph

Source: v4 §14, §15, §16, §19.

- [ ] Define `AgentState`.
- [ ] Add idempotent reducers.
- [ ] Add `GraphRuntimeService` facade.
- [ ] Add Planner-ReAct graph.
- [ ] Add `RunBudget`.
- [ ] Add `AgentError`.
- [ ] Add `ToolResultHandlerRegistry`.
- [ ] Add default handler and first tool handler.
- [ ] Add tests for reducer idempotency.

Hard rules:

- Nodes return state patches.
- Side effects go through ports/services/sinks.
- Runner must not branch with `if/elif` by `tool_name`.

## 11. Phase 6: Durable HITL

Source: v4 §17.

- [ ] Implement ask_user interrupt.
- [ ] Emit `HumanInputRequested`.
- [ ] Project to `TimelineWaitInputDisplayed`.
- [ ] Resume with `ResumeRunCommand`.
- [ ] Validate resume token and session state.

Acceptance:

- Waiting tasks survive process restart.
- Resume continues from interrupt point.

## 12. Phase 7: V1 Release Gate

- [ ] Single graph passes golden set.
- [ ] Old project golden samples are compared as behavior reference.
- [ ] No user traffic is routed until the golden set passes.
- [ ] ADR-009, ADR-010, ADR-015, ADR-023, ADR-024, ADR-025, ADR-027 are written.

Acceptance:

- V1 is demoable, recoverable, measurable, and protected by tests.

## 13. Important Paths

```text
Plan:
  /home/zym/k8s/sunmoonai/docs/mooc-manus-langgraph-longterm-plan-v4.md

Target source:
  /home/zym/research-app/research-admin-backend/app

Task docs:
  /home/zym/research-app/research-admin-backend/docs/mooc-manus-v4-rebuild-task.md
  /home/zym/research-app/research-admin-backend/docs/mooc-manus-v4-phase-0-inventory.md

Old reference only:
  /home/zym/imooc-mas/mooc-manus
  /home/zym/imooc/imooc-mas/mooc-manus

Platform deployment templates:
  /home/zym/k8s/sunmoonai/app-platform/research-app
```

## 14. Current Next Step

Start Phase 0 only:

1. Confirm dependency/checkpointer choices.
2. Design the smallest tables needed by the skeleton.
3. Implement the two-node graph and validation script.
4. Stop before building the full Planner-ReAct graph.
