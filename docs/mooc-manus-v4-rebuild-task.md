# MoocManus v4 Rebuild Task

Status: Phase 0 in progress
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

## 1. M1 Target

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

## 2. M1/M2 Scope Discipline

This task is not only a Phase 0 spike. The final objective is to implement
the v4 plan in research-app through the v4 milestone order:

```text
M1: build, validate, and release the first recoverable single-graph product slice.
M2: add the delayed platform capabilities only after M1 is real and measured.
```

Phase 0 is the first mandatory slice because it validates the physical runtime
assumptions. It is a gate, not the end state.

M1 includes only what is needed for a single graph to run, recover, stream, and be evaluated.

M1 must include:

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

M1 explicitly does not include:

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

M2 starts only after the M1 release gate. It is still part of the complete v4
roadmap, but must be triggered by real product/load needs rather than pulled
into the M1 implementation.

M2 roadmap:

- GraphRegistry with graph versioning, rollout, and active-run pinning.
- TransportMessage envelope and multi-broker boundaries.
- IntegrationEvent over RabbitMQ for cross-app collaboration.
- CQRS/materialized read models for timeline and summaries.
- Multi-provider Model Gateway, fallback, routing, and deeper prompt registry.
- Supervisor/multi-agent orchestration.
- Deep RAGFlow/knowledge-app integration.
- Full multi-tenant SecurityContext propagation, quotas, and stronger isolation.
- Long-term memory Store integration beyond the M1 interface/placeholder.
- EvidenceAssembler and multi-source evidence packaging.
- Production data scaling: partitioning, archiving, retention, and governance.

## 3. Phase -1: App Platform Boundary

- [x] Confirm target source is `/home/zym/research-app/research-admin-backend/app`.
- [x] Confirm deployment templates live under `/home/zym/k8s/sunmoonai/app-platform/research-app`.
- [x] Confirm existing deployment entrypoints:
  - `deploy-research-app-all/deploy-research-app-all.sh`
  - `deploy-research-app-all/deploy-research-app-all.conf`
  - `research-admin-backend/...`
  - `celeryworker-research-admin-backend/...`
- [x] Confirm old source is reference only:
  - `/home/zym/imooc-mas/mooc-manus`
  - `/home/zym/imooc/imooc-mas/mooc-manus`
- [x] Confirm Python agent runtime belongs to `research-admin-backend` and Celery worker, not Node BullMQ.
- [ ] Keep this boundary documented in ADR-027.
- [ ] Keep deployment requirements documented in this task file and the handoff.

Acceptance:

- No old project is deployed, imported, or used as fallback.
- New code lands only in the target source tree.
- Deployment changes, when required by M1, land in `/home/zym/k8s/sunmoonai/app-platform/research-app`.

## 3.1 v4 Coverage Matrix

Every v4 core section must map to an implementation task or an explicit M2
defer decision.

| v4 Area | M1 Action | M2 / Deferred Action |
|---|---|---|
| §0 M1/M2 scope | Enforce M1/M2 discipline in tasks and ADR-024. | Revisit M2 only after M1 release gate. |
| §1 LangGraph/LangChain role | Add LangGraph/LangChain only behind ACL adapters. | None. |
| §3 Old project | Use old project only for golden samples and behavior reference. | None. |
| §4 Architecture boundaries | Create domain/application/infrastructure/runtime package boundaries. | Runtime may later extract to service. |
| §5 App Platform | Use research-admin-backend + Celery worker; leave Node BullMQ out of graph execution. | K8s/sandbox hardening and possible dedicated sandbox service. |
| §5 Deployment | Reuse research-app deploy-all entrypoint; deploy API + Celery worker with shared image and separated runtime env. | Autoscaling, sandbox manager, and stronger runtime isolation. |
| §6 Execution topology | API creates run, enqueues Celery, worker executes graph, Redis/SSE streams UIEvent/LiveDelta. | RabbitMQ/IntegrationEvent only when cross-app events are needed. |
| §6.5 Walking Skeleton | Mandatory first vertical slice with checkpoint/interruption/SSE/idempotency validation. | None. |
| §7 Ports | Define domain ports for LLM, tools, checkpoint, memory, event sink, sandbox, files, knowledge. | TransportPort stays M2. |
| §8 Control/Data plane | Use code/YAML constants for M1 effective config. | Versioned GraphSpec/AgentProfile publishing. |
| §9 Principles/ACL | Add import boundary checks and naming rules. | Expand governance as platform grows. |
| §10 Domain models | Implement UserInput, StoredMessage, minimal Commands, RunLineage, DomainEvent/UIEvent, Plan/Step. | Full event class families and Command bus. |
| §11 Message mapping | Implement BaseMessage <-> StoredMessage serializer, validators, upcaster skeleton. | Broader schema migration tooling. |
| §12 Memory | Implement AgentMemory/MemoryPolicy and session summary/compaction. Leave LongTermMemory interface placeholder. | Long-term Store extraction/retrieval/attribution. |
| §13 RAGFlow | Define KnowledgePort and optional read-only retrieve adapter boundary. | Deep RAGFlow ask/ingest/KnowledgeAgent/EvidenceAssembler. |
| §14 LangGraph runtime | Build Phase 0 two-node graph first, then Planner-ReAct single graph with idempotent reducers. | Supervisor/multi-agent. |
| §15 Replay safety | Implement reducer idempotency, tool_call_id side-effect cache, session lock, idempotency_key, resume token. | GraphRegistry/run version pinning. |
| §16 Errors/budget | Implement AgentError and RunBudget checks. | Provider-level routing optimization. |
| §17 HITL | Implement ask_user interrupt/resume and waiting/running state transitions. | Approval UI and advanced HITL modes. |
| §18 Events/SSE | Implement DBEventSink, TimelineProjector, session_events schema, UIEvent replay, LiveDelta/final reconciliation. | TransportMessage envelope, CQRS read models, TraceSink. |
| §19 Tools | Implement ToolExecutionPort, permissions, ToolResultHandlerRegistry, default/simple handlers. | Full tool suite, approval UI, advanced artifact flows. |
| §20 Multi-agent | Single Planner-ReAct graph only. | Supervisor and specialized agents. |
| §21 SecurityContext | Add fixed/single-tenant SecurityContext structure and pass through queue/tools. | Full tenant propagation, quotas, isolation. |
| §22 Sandbox | Add SandboxPort and local/dev side-effect implementation; design K8sPodSandbox boundary. | Pooling, quota, gVisor/Kata, sandbox manager. |
| §23 Model/Prompt | Add LLMPort and one provider adapter or deterministic fake for tests; prompt constants with prompt_id. | Multi-provider gateway/fallback/prompt registry. |
| §24 Persistence | Add sessions, agent_runs, session_events, optional tool_side_effects; include schema_version/lineage fields. | agent_memories/long_term_memories/session_files scaling and partitioning. |
| §25 Config | Code/YAML config only. | Versioned config publishing. |
| §26 Runtime boundary | Runtime package exposes facade only. | Extract runtime to service if load requires. |
| §27 Safety | Add prompt-injection and tool-permission checks at adapters. | Stronger governance/audit flows. |
| §28 Observability/eval | Structured logs with RunLineage; golden harness starts from Phase 0. | TraceSink and expanded eval metrics. |
| §29 Tests | Unit/integration/golden tests for M1 gates. | Production-scale eval suites. |
| §30 Directory | Follow recommended package layout without introducing M2-only modules early. | Add M2 modules when triggered. |
| §31 Roadmap | Execute Phase 0 -> 0.5 -> 1..7. | Execute M2-A..I by trigger. |
| §32 ADRs | Write required M1 ADRs. | Write/update M2 ADRs when M2 starts. |

## 4. Phase 0: Walking Skeleton

Source: v4 §6.5 and §35 task 0.

Purpose: validate the three physical assumptions before building the full
platform. This is not a toy unit test. It must run through the real app shape:
FastAPI -> Celery -> LangGraph -> Postgres checkpoint/events -> Redis/SSE.

Must validate:

- [x] Checkpoint + interrupt + resume work in the local worker process.
- [x] Postgres checkpoint can resume by `thread_id=session_id` across separate DB/checkpointer connections.
- [x] SSE endpoint replays persisted UIEvents after `last_event_id` before subscribing to Redis.
- [x] Tool side effect idempotency uses a unique `tool_call_id` guard.

Current limitation:

- Phase 0 has validated Postgres checkpoint persistence across connections, which is the important worker-restart prerequisite. A real Celery process kill/restart validation is still useful once the broker/worker process is part of the local validation harness.
- The runtime database user remains least-privilege and has no schema DDL permission. Alembic uses optional `MIGRATION_DATABASE_URL` for migrations.

Explicit non-goals:

- Do not implement the full Planner-ReAct graph.
- Do not introduce GraphRegistry, TransportMessage, CQRS read models, or multi-agent orchestration.
- Do not build long-term memory; only add the minimum stable hooks needed by the skeleton.
- Do not route user traffic to this path until the M1 release gate passes.

Minimal graph:

```text
START
  -> ask_user_node
  -> side_effect_tool_node
  -> END
```

Node contract:

- `ask_user_node`
  - emits a domain event meaning human input is required.
  - projects that event to a UIEvent for the timeline.
  - updates session/run state to waiting.
  - calls LangGraph interrupt with a JSON-serializable payload.
- `side_effect_tool_node`
  - resumes only after validated user input.
  - writes one deterministic side effect through an idempotency guard.
  - emits requested/completed events and timeline projections.
  - can be replayed from checkpoint without duplicating the side effect.

Minimum implementation checklist:

- [x] Add `langgraph` dependency.
- [x] Add `langchain-core` dependency.
- [x] Choose/check M1 Postgres checkpointer dependency.
- [x] Confirm whether `langgraph-checkpoint-postgres` manages its own checkpoint tables or needs an Alembic-owned wrapper.
- [x] Add LangGraph checkpoint tables to Alembic because the worker runtime account must not run DDL.
- [x] Add minimal run/session/event tables needed by the skeleton:
  - `agent_sessions`
  - `agent_runs`
  - `session_events`
  - `tool_side_effects`
- [x] Add minimal domain models:
  - `UserInput`
  - minimal `CreateRunCommand` / `ResumeRunCommand`
  - `RunLineage`
  - minimal `DomainEvent` / `UIEvent`
- [x] Add repositories:
  - session repository
  - run repository
  - event repository
  - side-effect idempotency repository/cache
- [x] Add `DBEventSink` and `TimelineProjector`.
- [x] Add a minimal Celery task that runs the graph.
- [x] Add ask-user interrupt path.
- [x] Add resume API/command path.
- [x] Add side-effect cache keyed by `tool_call_id`.
- [x] Add Redis/SSE stream and `last_event_id`补发 path.
- [x] Add minimal API:
  - `POST /api/agent/sessions`
  - `POST /api/agent/sessions/{session_id}/runs`
  - `POST /api/agent/runs/{run_id}/resume`
  - `GET /api/agent/sessions/{session_id}/events`
  - `GET /api/agent/sessions/{session_id}/stream`
- [x] Add a validation script for the Phase 0 acceptance checks covered by the local/remote harness.
- [x] Add initial unit tests for LangGraph interrupt/resume and TimelineProjector.

Recommended file layout:

```text
app/domain/agent/
  commands.py
  events.py
  models.py

app/application/agent/
  run_service.py
  event_sink.py
  timeline_projector.py

app/infrastructure/agent/
  repositories.py
  side_effect_store.py

app/infrastructure/graph/
  checkpointer.py
  walking_skeleton.py

app/interfaces/api/
  agent.py

app/tasks/
  agent_graph.py

tests/agent/
  test_walking_skeleton.py
```

If the repo already has a more specific local convention, follow that
convention and keep this layout as the intent, not as a forced directory shape.

Minimal schema draft:

```text
sessions(
  id,
  status,
  created_at,
  updated_at
)

agent_runs(
  id,
  session_id,
  graph_name,
  graph_version,
  thread_id,
  idempotency_key,
  status,
  waiting_reason,
  resume_token,
  started_at,
  completed_at,
  error,
  created_at,
  updated_at
)

session_events(
  id,
  session_id,
  run_id,
  sequence_no,
  category,
  event_type,
  payload_schema_version,
  lineage,
  payload,
  metadata,
  created_at
)

tool_side_effects(
  tool_call_id,
  run_id,
  status,
  result,
  created_at,
  updated_at
)
```

Notes:

- `thread_id` must equal `session_id` for Phase 0 unless a concrete reason is documented.
- `session_events.sequence_no` must be monotonic within a session.
- `session_events.id` is the SSE `last_event_id` replay cursor unless a better cursor is explicitly documented.
- `tool_side_effects.tool_call_id` must be unique.
- Do not add a `state` table. LangGraph checkpoint is the runtime state persistence.

Minimal API contract:

```text
POST /api/agent/sessions
  -> { session_id, status }

POST /api/agent/sessions/{session_id}/runs
  body: { idempotency_key?: string }
  -> { run_id, session_id, status }

POST /api/agent/runs/{run_id}/resume
  body: { resume_token: string, user_input: string, idempotency_key?: string }
  -> { run_id, session_id, status }

GET /api/agent/sessions/{session_id}/events?after_event_id=<id>
  -> { events: UIEvent[] }

GET /api/agent/sessions/{session_id}/stream?last_event_id=<id>
  -> text/event-stream
```

Minimal UIEvent types:

```text
TimelineRunStarted
TimelineWaitInputDisplayed
TimelineUserInputReceived
TimelineToolStarted
TimelineToolCompleted
TimelineRunCompleted
TimelineRunFailed
```

Implementation sequence:

1. Add dependencies and confirm imports.
2. Add Alembic migration for the minimal schema.
3. Add repositories and event append/query helpers.
4. Add `DBEventSink` and `TimelineProjector`.
5. Add API routes with no graph execution inside the HTTP request lifecycle.
6. Add Celery task `run_agent_graph(run_id)`.
7. Add LangGraph walking skeleton with `thread_id=session_id`.
8. Add Redis publish from persisted UIEvent.
9. Add SSE endpoint that first replays persisted UIEvents after `last_event_id`, then streams live Redis events.
10. Add side-effect idempotency guard keyed by `tool_call_id`.
11. Add validation script/test and record the result in the handoff.

Acceptance:

```text
1. Trigger graph -> ask_user -> interrupt -> session.status=waiting. DONE: remote Postgres/Redis integration path passed.
2. Kill/restart worker -> resume continues from checkpoint. PARTIAL: cross-connection Postgres checkpoint resume passes; real Celery process restart remains to script.
3. User input resumes graph -> tool node executes -> side effect happens once. DONE: remote Postgres/Redis integration path passed.
4. Forced replay after tool node -> side effect is not repeated. DONE: `tool_side_effects` stayed at one row for the same `tool_call_id`.
5. SSE reconnect with last_event_id -> timeline complete, no missing/duplicate events. PARTIAL: replay-before-subscribe code exists and HTTP `/events?after_event_id=` replay is covered; real network-level SSE disconnect/reconnect script is still pending.
```

Validation script shape:

```text
scripts/validate_walking_skeleton.py

1. create session.
2. start run with a stable idempotency_key.
3. wait until `TimelineWaitInputDisplayed` appears.
4. restart/kill worker manually or through a test hook.
5. resume with the returned resume_token.
6. assert `TimelineToolCompleted` appears once.
7. replay/resubmit the same graph state or same tool_call_id path.
8. assert side-effect row/result count is still one.
9. reconnect events/stream using the previous last_event_id.
10. assert no missing or duplicate UIEvent ids.
```

Definition of done:

- All five acceptance checks pass locally.
- The validation can be rerun without manual database cleanup beyond the normal test setup.
- The script/test becomes the first golden case in Phase 0.5.
- Handoff records changed files, validation command, result, and next smallest task.

Current validation snapshot, 2026-07-08:

```bash
cd /home/zymun/research-app/research-admin-backend/app
.venv/bin/pytest
.venv/bin/pyright
uv run python -m compileall app core scripts
ENV=production LOG_LEVEL=WARNING uv run python -u scripts/validate_agent_phase0.py
```

Result:

- `pytest`: 2 passed.
- `pyright`: 0 errors.
- `compileall`: passed.
- `alembic upgrade head --sql`: rendered successfully.
- `MIGRATION_DATABASE_URL=... uv run alembic upgrade head`: applied successfully against remote `research_admin`.
- Runtime DB user can see Phase 0 tables but still has no schema DDL permission.
- LangGraph Postgres checkpointer resumed across separate connections with the same `thread_id`.
- Remote Postgres/Redis integration flow passed:
  - created `agent_sessions` and `agent_runs`
  - first graph run reached `waiting`
  - resume completed the run
  - UI timeline: `TimelineRunStarted`, `TimelineWaitInputDisplayed`, `TimelineUserInputReceived`, `TimelineToolStarted`, `TimelineToolCompleted`, `TimelineRunCompleted`
  - `last_event_id` replay returned exactly the events after the cursor
  - HTTP `/api/agent/sessions/{session_id}/events` full replay and cursor replay passed through ASGI
  - forced replay kept `tool_side_effects` count at `1`
- `scripts/validate_agent_phase0.py`: passed against remote Postgres/Redis.
- The SSE endpoint serializes persisted UIEvents safely and replays them before subscribing to Redis. The remaining gap is a real network-level SSE disconnect/reconnect test against a running server, not ASGITransport's in-process stream.
- Redis ACL user `research_admin_backend` was recreated/updated and can publish to `agent:*` channels.

Previously blocked validation:

- Direct `phase0_postgres_checkpointer(setup=True)` reached the remote Postgres but failed with `permission denied for schema public`. This is expected for a least-privilege runtime account and is now resolved by Alembic-owned checkpoint tables.
- The current `DATABASE_URL` user has `USAGE` but not `CREATE` on `public`, is not a `pg_database_owner` member, and cannot create a new schema. This remains the intended runtime boundary.

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
- [ ] Add import-boundary checks:
  - domain models cannot import `langgraph` or `langchain_core`.
  - application services cannot directly import `langgraph`.
  - LangGraph/LangChain types live only in runtime/adapters/mappers.
- [ ] Add ADR-001, ADR-002, ADR-003, ADR-010, ADR-022.

Hard rules:

- No `Message = UserInput` legacy alias.
- `Command` names request intent.
- `Event` names facts that already happened.
- Domain models must not import LangGraph.

## 7. Phase 2: EventSink and Projection

Source: v4 §10.4, §18, §24.

- [ ] Define `RunLineage`.
- [ ] Define M1 `DomainEvent`.
- [ ] Define M1 `UIEvent`.
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
- [ ] Add fixed/single-tenant `SecurityContext` placeholder and pass it through the queue/tool boundary.
- [ ] Add structured logs with `RunLineage`.
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
- [ ] Add memory tests for windowing, summary, schema_version, and safety flags.

M1 only:

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
- [ ] Add `ToolExecutionPort`.
- [ ] Add `LLMPort` and one provider/fake adapter.
- [ ] Add tool permissions.
- [ ] Add tool side-effect idempotency by `tool_call_id`.
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

## 12. Phase 7: M1 Release Gate

- [ ] Single graph passes golden set.
- [ ] Old project golden samples are compared as behavior reference.
- [ ] Phase 0 checkpoint/SSE/replay acceptance still passes.
- [ ] Deployment templates support the M1 runtime:
  - API deployment has required config/env for agent sessions, Postgres checkpoint/event tables, Redis/SSE, and Celery producer.
  - `celeryworker-research-admin-backend` runs the same backend image and starts the graph-runner Celery worker.
  - The application reads `CELERY_BROKER_URL`; k8s injects producer credentials for API and worker credentials for Worker.
  - ConfigMap/Secret additions are generated through the existing k8s template/generate flow, not hard-coded in application code.
- [ ] `deploy-research-app-all.sh validate-resources --cluster KIND` or equivalent dry validation succeeds.
- [ ] A controlled KIND deployment verifies pods, logs, API health, Celery worker startup, and the Phase 0/M1 validation flow against deployed services.
- [ ] No user traffic is routed until the golden set passes.
- [ ] Required ADRs exist:
  - ADR-001, ADR-002, ADR-003
  - ADR-009, ADR-010
  - ADR-013, ADR-015, ADR-016
  - ADR-019, ADR-021, ADR-022
  - ADR-023, ADR-024, ADR-025, ADR-026, ADR-027

Acceptance:

- M1 is demoable, recoverable, measurable, deployable, and protected by tests.

## 12.1 Phase 8: Deployment Closure

This phase is intentionally late: do not start by reshaping k8s before the
runtime contract exists. Once Phase 0 proves the skeleton and Phase 7 is close,
sync deployment the way `info-app` does: source work and platform work are both
tracked, and the handoff records exactly what was deployed or deferred.

Scope:

- [ ] Update `/home/zym/k8s/sunmoonai/app-platform/research-app` only for real runtime needs discovered by M1.
- [ ] Ensure `research-admin-backend` image contains the LangGraph runtime, API endpoints, Alembic migrations, and Celery tasks.
- [ ] Ensure `celeryworker-research-admin-backend` starts the correct worker command/queue for graph execution.
- [ ] Add or update ConfigMap/Secret templates for M1-only variables:
  - checkpoint/event database URL or existing database secret wiring
  - Redis URL/session lock/SSE settings
  - Celery broker URL injection
  - feature flag to keep v4 traffic disabled until release gate passes
  - model/tool provider credentials only when the Planner-ReAct phase needs them
- [ ] Keep `nodebullworker-research-web-backend` out of LangGraph execution.
- [ ] Record image tags, deploy command, cluster, validation commands, and known deploy gaps in `docs/mooc-manus-v4-handoff.md`.

Validation:

```bash
cd /home/zym/k8s/sunmoonai/app-platform/research-app/deploy-research-app-all
./deploy-research-app-all.sh validate-resources --cluster KIND
./deploy-research-app-all.sh deploy --cluster KIND
./deploy-research-app-all.sh status --cluster KIND
```

Release gate:

- API pod is healthy.
- Celery worker pod is healthy and consumes the graph queue.
- Phase 0/M1 validation flow passes against the deployed API.
- SSE reconnect/replay works through the deployed ingress/service path if that path is enabled.
- No frontend/user route is enabled before the golden set passes.

## 12.2 M2 Roadmap

M2 is required for the complete v4 platform, but starts only after M1 is stable
and measured.

| M2 Track | Trigger | Deliverables |
|---|---|---|
| M2-A GraphRegistry | Need graph rollout/version pinning. | GraphSpec, immutable versions, active-run pinning, rollout controls. |
| M2-B Model Gateway | Need second provider, fallback, routing, cost control. | Multi-provider adapters, fallback policy, prompt registry. |
| M2-C Long-term Memory | Single graph quality needs memory improvement. | extraction/retrieval node, recall attribution, safety filtering, confirmation. |
| M2-D Multi-tenant Security | Real multi-user/tenant launch. | SecurityContext propagation, quotas, namespace isolation, bulkheads. |
| M2-E Knowledge/RAGFlow | Knowledge question answering is core. | ask/ingest, KnowledgeAgent, citations, permissioned document flows. |
| M2-F Multi-agent | Single agent no longer enough. | Supervisor, AgentProfile, handoff, agent-level memory/tool isolation. |
| M2-G Data Scale | Event/read pressure grows. | partitioning, archiving, CQRS read models, run summaries. |
| M2-H Transport/Observability | Multi-app or stronger observability needed. | TransportMessage, IntegrationEvent/RabbitMQ, TraceSink. |
| M2-I EvidenceAssembler | Cross-source evidence enters prompt. | EvidenceBlock, multi-source dedupe/rerank/token budget/raw readback. |

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

## 14. Testing Contract

Minimum M1 tests:

- message serializer round-trip
- message sequence validation
- upcaster skeleton behavior
- Command -> DomainEvent mapping
- DomainEvent -> UIEvent projection
- session_events ordering and `last_event_id` replay
- run idempotency_key duplicate submit
- Redis session lock behavior
- reducer idempotency
- tool_call_id side-effect idempotency
- ask_user interrupt/resume
- worker restart resume by `thread_id=session_id`
- SSE reconnect no missing/duplicate UIEvent
- ACL import-boundary check
- golden harness deterministic replay

## 15. Implementation Rules

- Keep changes scoped to `research-admin-backend` until a phase explicitly needs frontend or k8s. Phase 7/8 explicitly includes k8s deployment closure.
- Do not copy old MoocManus source.
- Do not add a `state` table.
- Do not put TransportMessage in M1 domain.
- Do not implement GraphRegistry in M1.
- Do not route graph control through events.
- Do not expose raw LangGraph events to frontend consumers.
- Do not write long-term memory automatically before its M2 governance exists.
- Do not connect user traffic before the M1 release gate passes.

## 16. Progress Recording Template

Borrow the `info-app` working style: each meaningful step should leave a
recoverable record, not just code.

For every completed task or pause point, update the handoff or this file with:

```markdown
### TASK: <short name>

- Status: pending / in_progress / complete / blocked
- Date: YYYY-MM-DD

#### Goal

<one sentence>

#### In Scope

- <concrete item>

#### Out of Scope

- <explicit exclusion>

#### Changed Files

| File | Change | Notes |
|---|---|---|
| `path/to/file` | added/modified | <short note> |

#### Validation

| Command | Result | Notes |
|---|---|---|
| `<command>` | pass/fail/not run | <short note> |

#### Known Issues

- <issue or none>

#### Next Smallest Task

- <next concrete step>
```

## 17. Current Execution Contract

Implement the full v4 plan in order. This file is the authoritative
implementation backlog.

Do not skip ahead to M2 or the full Planner-ReAct graph before Phase 0 is
green. Do not stop after Phase 0 either; Phase 0 proves the base, then the
work continues through the M1 phases and finally M2 when justified.

Immediate next slice: Phase 0.

1. Confirm dependency/checkpointer choices.
2. Design the smallest tables needed by the skeleton.
3. Implement the two-node graph and validation script.
4. Stop at the Phase 0 acceptance gate.

After Phase 0 passes:

1. Turn the Phase 0 validation into the first golden case.
2. Continue through Phases 1-7 until M1 is demoable, recoverable, measurable,
   deployable, protected by tests, and ready for controlled user traffic.
3. Close Phase 8 deployment work before routing user traffic.
4. Revisit the M2 roadmap item by item with evidence from real usage.
