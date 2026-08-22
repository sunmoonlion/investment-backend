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

The first graph must be deliberately tiny. It should validate checkpoint, SSE reconciliation, and side-effect idempotency before the larger M1 graph runtime begins.

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
- Lightweight AgentProfile / EffectiveAgentConfig switching inside the single graph.
- Context / State / Memory / Workspace / Database / Object Storage layer checks.
- Strategy / Visitor / Handler Registry constraints at the supported variation points.
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
- Workspace/Project entity and `workspaces` / `projects` tables.

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
- Workspace/Project as a product organization layer for sessions, files, knowledge bindings, default AgentProfile, and permissions.
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
- [x] Confirm the v4 frontend pairing decision:
  - `research-admin-backend` is the single Python application backend in M1, not only an admin-only backend.
  - `/api/admin/**` is the management API face for `research-admin-frontend`.
  - `/api/agent/**` is the end-user Agent API face for `research-web-frontend` (Next.js) to consume directly.
  - `research-web-backend` / Node BFF stays out of the M1 Agent critical path.
- [x] Keep this boundary documented in ADR-027.
- [x] Keep frontend pairing documented in v4 §5.6 / ADR-031 / ADR-032 and mirrored in this task file.
- [x] Keep deployment requirements documented in this task file and the handoff.

Acceptance:

- No old project is deployed, imported, or used as fallback.
- New code lands only in the target source tree.
- Deployment changes, when required by M1, land in `/home/zym/k8s/sunmoonai/app-platform/research-app`.
- Agent frontend work, when explicitly started, lands in `research-web-frontend` and consumes only `/api/agent/**`.
- Admin frontend work, when explicitly started, lands in `research-admin-frontend` and consumes only `/api/admin/**`.

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
| §5.6 Frontend pairing | Treat `research-admin-backend` as the M1 Python application backend with two API faces: `/api/admin/**` for admin UI and `/api/agent/**` for end-user Agent UI. `research-web-frontend` (Next.js) talks directly to `/api/agent/**`; Node web-backend is not in the Agent critical path. | Node BFF only when SSR/SEO, cross-app aggregation, or Node-side auth gateway is justified. Physical split into admin-api / agent-api only when scaling/security/release cadence demands it. |
| §5 Deployment | Reuse research-app deploy-all entrypoint; deploy API + Celery worker with shared image and separated runtime env. | Autoscaling, sandbox manager, and stronger runtime isolation. |
| §6 Execution topology | API creates run, enqueues Celery, worker executes graph, Redis/SSE streams UIEvent/LiveDelta. | RabbitMQ/IntegrationEvent only when cross-app events are needed. |
| §6.5 Walking Skeleton | Mandatory first vertical slice with checkpoint/interruption/SSE/idempotency validation. | None. |
| §7 Ports | Define domain ports for LLM, tools, checkpoint, memory, event sink, sandbox, files, knowledge. | TransportPort stays M2. |
| §8 Control/Data plane | Use code/YAML constants for M1 effective config. | Versioned GraphSpec/AgentProfile publishing. |
| §9 Principles/ACL | Add import boundary checks and naming rules. | Expand governance as platform grows. |
| §9.6.1 / §26.1 Layering | Add no-mixing tests for Context, State, Memory, Workspace, Database, Object Storage. M1 reserves `project_id` only as an optional placeholder and does not create Workspace entities. | Workspace/Project product organization layer and object/file governance. |
| §10 Domain models | Implement UserInput, StoredMessage, minimal Commands, RunLineage, DomainEvent/UIEvent, Plan/Step. | Full event class families and Command bus. |
| §11 Message mapping | Implement BaseMessage <-> StoredMessage serializer, validators, upcaster skeleton. | Broader schema migration tooling. |
| §12 Memory | Implement AgentMemory/MemoryPolicy and session summary/compaction. Leave LongTermMemory interface placeholder. | Long-term Store extraction/retrieval/attribution. |
| §13 RAGFlow | Define KnowledgePort and optional read-only retrieve adapter boundary. | Deep RAGFlow ask/ingest/KnowledgeAgent/EvidenceAssembler. |
| §14 LangGraph runtime | Build Phase 0 two-node graph first, then the first M1 graph with `BaseAgentState`, graph-specific state, and idempotent reducers. Planner-ReAct is only the first concrete graph shape, not the platform architecture. | Additional graph shapes, GraphRegistry, Supervisor/multi-agent. |
| §15 Replay safety | Implement reducer idempotency, tool_call_id side-effect cache, session lock, idempotency_key, resume token. | GraphRegistry/run version pinning. |
| §16 Errors/budget | Implement AgentError and RunBudget checks. | Provider-level routing optimization. |
| §17 HITL | Implement ask_user interrupt/resume and waiting/running state transitions. | Approval UI and advanced HITL modes. |
| §18 Events/SSE | Implement DBEventSink, TimelineProjector, session_events schema, UIEvent replay, LiveDelta/final reconciliation. | TransportMessage envelope, CQRS read models, TraceSink. |
| §18 Frontend event contract | Frontend consumers receive UIEvent and LiveDelta only; no raw LangGraph events or legacy overloaded Event payloads. | TransportMessage envelope and richer read models wait for M2. |
| §19 Tools | Implement ToolExecutionPort, permissions, ToolResultHandlerRegistry, default/simple handlers. | Full tool suite, approval UI, advanced artifact flows. |
| §20 AgentProfile / Multi-agent | M1 supports lightweight AgentProfile switching inside one graph runtime: different prompt/tools/permissions/model/memory policy without forking graph code. | AgentRegistry, GraphRegistry, Supervisor and specialized agents. |
| §21 SecurityContext | Add fixed/single-tenant SecurityContext structure and pass through queue/tools. | Full tenant propagation, quotas, isolation. |
| §22 Sandbox | Add SandboxPort and local/dev side-effect implementation; design K8sPodSandbox boundary. | Pooling, quota, gVisor/Kata, sandbox manager. |
| §23 Model/Prompt | Add LLMPort and one provider adapter or deterministic fake for tests; prompt constants with prompt_id. | Multi-provider gateway/fallback/prompt registry. |
| §24 Persistence | Add sessions, agent_runs, session_events, optional tool_side_effects; include schema_version/lineage fields. | agent_memories/long_term_memories/session_files scaling and partitioning. |
| §25 Config | Code/YAML config only. | Versioned config publishing. |
| §26 Runtime boundary / patterns | Runtime package exposes facade only. Use Protocol + registry + small handlers for ToolResultHandler, TimelineProjector, MemoryPolicy, LLMPort, SandboxPort, and future EvidenceAssembler. | Extract runtime to service if load requires; deeper strategy sets when product needs them. |
| §27 Safety | Add prompt-injection and tool-permission checks at adapters. | Stronger governance/audit flows. |
| §28 Observability/eval | Structured logs with RunLineage; golden harness starts from Phase 0. | TraceSink and expanded eval metrics. |
| §29 Tests | Unit/integration/golden tests for M1 gates. | Production-scale eval suites. |
| §30 Directory | Follow recommended package layout without introducing M2-only modules early. | Add M2 modules when triggered. |
| §31 Roadmap | Execute Phase 0 -> 0.5 -> 1..7. | Execute M2-A..I by trigger. |
| §32 ADRs | Write required M1 ADRs. | Write/update M2 ADRs when M2 starts. |
| ADR-031 / ADR-032 | Mirror frontend pairing and mooc-manus/ui absorption into the implementation backlog. | Revisit if M2 introduces a Node BFF or separates Python API services. |

## 3.2 Frontend Pairing And UI Absorption Boundary

Source: v4 §5.6, §18, ADR-031, ADR-032.

Decision:

```text
research-admin-backend is the M1 Python application backend.
It exposes two audience-facing API surfaces:

  /api/admin/**  -> management API face, consumed by research-admin-frontend
  /api/agent/**  -> end-user Agent API face, consumed directly by research-web-frontend

research-web-backend / Node BFF is not part of the M1 Agent critical path.
```

Rationale:

- Agent execution, checkpoint, interrupt/resume, SSE replay, UIEvent projection, and LiveDelta reconciliation all live in the Python/LangGraph runtime.
- Adding Node web-backend as a BFF in M1 would duplicate streaming/reconnect semantics and create another place where event ordering can drift.
- The `admin` name in `research-admin-backend` is treated as a platform language-stack label, not as an audience boundary. Audience boundaries are enforced by route prefix, auth scope, and audit.

Implementation rules:

- `research-web-frontend` may call only `/api/agent/**` for Agent product flows.
- `research-admin-frontend` may call only `/api/admin/**` for management flows.
- End-user tokens must not reach `/api/admin/**`.
- Staff-initiated end-user runs require explicit impersonation/audit fields before they are allowed.
- Do not route Agent run/start/resume/SSE through `research-web-backend` in M1.
- If a future Node BFF appears, it is a proxy/SSR/auth aggregation layer only and never becomes the source of truth for session state, events, checkpoint, memory, or run status.

`mooc-manus/ui` absorption boundary:

```text
Absorb into research-web-frontend:
  - Next/React/Tailwind/Radix/shadcn shell style.
  - VNC sandbox viewer interaction.
  - SSE typewriter timeline shape.
  - Tool call cards.
  - Markdown and file preview patterns.
  - Interrupt / waiting-for-human interaction states.

Do not copy:
  - legacy overloaded Event payloads.
  - direct raw LangGraph event consumption.
  - old session/message/memory dict shapes.
  - any backend coupling that bypasses /api/agent/**.
```

Frontend event contract:

- Timeline consumes persisted `UIEvent` projections only.
- Live typing/progress consumes `LiveDelta` only.
- Frontend deduplicates by `UIEvent.id`.
- SSE reconnect uses `last_event_id` and replays only persisted UIEvents.
- `LiveDelta` may be dropped; final UIEvent is the reconciliation source.
- Frontend tests must prove no raw LangGraph event leaks into UI consumption.

Acceptance:

- A local or deployed `research-web-frontend` page can:
  1. create an Agent session via `/api/agent/sessions`;
  2. start a run via `/api/agent/sessions/{session_id}/runs`;
  3. subscribe to `/api/agent/sessions/{session_id}/stream`;
  4. render UIEvent timeline entries and LiveDelta increments separately;
  5. resume an interrupt via `/api/agent/runs/{run_id}/resume`;
  6. reconnect with `last_event_id` without duplicate or missing persisted UIEvents.
- No frontend code calls raw LangGraph event endpoints or `research-web-backend` for Agent run control.

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

- The runtime database user remains least-privilege and has no schema DDL permission. Alembic uses optional `MIGRATION_DATABASE_URL` for migrations.

Explicit non-goals:

- Do not implement the full first M1 graph runtime before the walking skeleton is green.
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
2. Kill/restart worker -> resume continues from checkpoint. DONE: `scripts/validate_deployed_agent_worker_restart.py` restarts the deployed Celery worker and resumes the waiting run.
3. User input resumes graph -> tool node executes -> side effect happens once. DONE: remote Postgres/Redis integration path passed.
4. Forced replay after tool node -> side effect is not repeated. DONE: `tool_side_effects` stayed at one row for the same `tool_call_id`.
5. SSE reconnect with last_event_id -> timeline complete, no missing/duplicate events. DONE: deployed HTTP replay and service-level SSE replay return the expected cursor-tail events.
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
- The SSE endpoint serializes persisted UIEvents safely and replays them before subscribing to Redis. Deployed service-level SSE replay passed through `kubectl port-forward`.
- Redis ACL user `research_admin_backend` was recreated/updated and can publish to `agent:*` channels.

Previously blocked validation:

- Direct `phase0_postgres_checkpointer(setup=True)` reached the remote Postgres but failed with `permission denied for schema public`. This is expected for a least-privilege runtime account and is now resolved by Alembic-owned checkpoint tables.
- The current `DATABASE_URL` user has `USAGE` but not `CREATE` on `public`, is not a `pg_database_owner` member, and cannot create a new schema. This remains the intended runtime boundary.

## 5. Phase 0.5: Minimal Evaluation Harness

Source: v4 §28.3 and §35 task 0.5.

- [x] Turn the Phase 0 walking skeleton behavior into the first golden case.
- [x] Add fixture format for golden tasks.
- [x] Add LLM recording/replay harness placeholder, even if Phase 0 graph uses no LLM.
- [x] Add CI/test command that can run the golden case deterministically.

Acceptance:

- [x] A future prompt/graph/tool change can be checked against at least one golden case.
- [x] No live LLM call is required for this first validation.

Current golden cases, 2026-07-08:

```bash
cd /home/zymun/research-app/research-admin-backend/app
uv run python scripts/run_agent_golden.py
uv run python scripts/run_agent_golden.py --fixture tests/golden/first_m1_graph.json
uv run pytest
```

Result:

- `tests/golden/phase0_walking_skeleton.json` fixes the Phase 0 inputs, expected resumed state, expected DomainEvent -> UIEvent projection, and expected timeline.
- `tests/golden/first_m1_graph.json` fixes the first M1 graph input, plan shape, assistant output, and zero-live-LLM expectation.
- `tests/golden/old_project_planner_react_reference.json` records the old Planner-ReAct behavior reference without importing old source.
- `scripts/run_agent_golden.py` dispatches fixture types without Postgres, Redis, Celery, or live LLM calls.
- `tests/test_agent_golden.py` makes all golden fixtures part of the normal test suite.
- `pytest`: 52 passed.

## 6. Phase 1: Semantic Freeze and Message Boundary

Source: v4 §9.2, §10, §11, §31.1.

- [x] Define `UserInput`.
- [x] Define `StoredMessage`.
- [x] Define minimal `CreateRunCommand`, `ResumeRunCommand`, `CancelRunCommand`.
- [x] Define serializer/upcaster skeleton.
- [x] Use LangChain Core `BaseMessage` only behind adapters/ACL.
- [x] Add tests for message sequence validity.
- [x] Add import-boundary checks:
  - domain models cannot import `langgraph` or `langchain_core`.
  - application services cannot directly import `langgraph`.
  - LangGraph/LangChain types live only in runtime/adapters/mappers.
- [x] Add ADR-001, ADR-002, ADR-003, ADR-010, ADR-022.

Hard rules:

- No `Message = UserInput` legacy alias.
- `Command` names request intent.
- `Event` names facts that already happened.
- Domain models must not import LangGraph.

Current implementation snapshot, 2026-07-08:

- `StoredMessage` and `MessageRole` live in `app/domain/agent/models.py`.
- `CancelRunCommand` lives with `CreateRunCommand` and `ResumeRunCommand` in `app/domain/agent/commands.py`.
- `upcast_stored_message` is the first schema-version gate for stored messages.
- LangChain `BaseMessage` conversion is isolated in `app/infrastructure/agent/message_mapper.py`.
- `tests/test_agent_message_boundary.py` covers current-schema upcast, LangChain adapter mapping, command naming, and import-boundary checks.
- ADR-001, ADR-002, ADR-003, ADR-010, and ADR-022 record the accepted semantic-boundary decisions.

## 7. Phase 2: EventSink and Projection

Source: v4 §10.4, §18, §24.

- [x] Define `RunLineage`.
- [x] Define M1 `DomainEvent`.
- [x] Define M1 `UIEvent`.
- [x] Define `EventSink`.
- [x] Define `TimelineProjector`.
- [x] Persist `session_events` with `category`, `payload_schema_version`, `lineage`, `payload`, `metadata`.
- [x] Add LiveDelta/final UIEvent reconciliation path.
- [x] Add ADR-013 and ADR-019.

Hard rules:

- DomainEvent is append-only truth.
- UIEvent is a rebuildable projection.
- Frontend consumes UIEvent or LiveDelta, never raw LangGraph events.
- Graph control flow must not be driven by events.

## 8. Phase 3: Execution Topology

Source: v4 §6, §15.3, §31.1.

- [x] API creates run and returns `run_id` immediately.
- [x] API enqueues graph-runner Celery task when Celery is configured.
- [x] Worker owns graph execution.
- [x] Add run state machine:
  `created -> running -> waiting <-> running -> completed/failed/cancelled/budget_exceeded`.
- [x] Add `idempotency_key` handling.
- [x] Add Redis session lock with TTL.
- [x] Add Redis session lock renewal.
- [x] Add fixed/single-tenant `SecurityContext` placeholder and pass it through the queue/tool boundary.
- [x] Add structured logs with `RunLineage`.
- [x] Add Redis Pub/Sub for UIEvent SSE.
- [x] Add LiveDelta Redis Pub/Sub/reconciliation.

Acceptance:

- No long agent run executes inside HTTP request lifecycle.
- Duplicate API submit does not create duplicate active runs.

Current implementation snapshot, 2026-07-09:

- `AgentRunService.create_run()` returns `run_id` immediately and dispatches `app.tasks.agent_graph.run` through Celery when `CELERY_BROKER_URL` is configured.
- `AgentRepository.create_run()` de-duplicates `(session_id, idempotency_key)` before inserting a new `agent_runs` row.
- `run_agent_graph` owns graph execution in the Celery worker path; FastAPI does not execute the long graph inline.
- `RedisSessionLock` lives in `app/infrastructure/agent/session_lock.py` and uses `{AGENT_REDIS_KEY_PREFIX}:session:{session_id}:lock` with `AGENT_REDIS_KEY_PREFIX=research:agent` and `AGENT_SESSION_LOCK_TTL_SECONDS`.
- The worker acquires the session lock before setting the run to `running`; lock contention records `RunFailed` with `code='session_locked'`.
- `RedisSessionLock.renew()` extends TTL only when the caller still owns the token; the worker renews before and after graph execution.
- Lock release intentionally uses Redis `GET`/`DEL` instead of Lua `EVAL` because the current runtime Redis ACL disallows `EVAL`.
- `SecurityContext` lives in `app/domain/agent/security.py` as a fixed single-tenant M1 placeholder.
- `CreateRunCommand`, `ResumeRunCommand`, and `CancelRunCommand` carry `SecurityContext`; `AgentRunService` serializes it through the Celery graph task boundary.
- `run_agent_graph` validates the received security context before execution and includes it in Phase 0 start/input/tool-start DomainEvent payloads.
- `lineage_log_extra()` lives in `app/application/agent/run_logging.py`; graph worker logs include `session_id`, `run_id`, `root_run_id`, and `parent_run_id` at loaded/start/waiting/completed/failed/lock-busy points.
- `validate_run_status_transition()` defines the M1 run state machine and `AgentRepository.set_run_status()` enforces it before updating `agent_runs.status`.
- `DBEventSink` publishes projected UIEvents to Redis Pub/Sub for the SSE endpoint and publishes `LiveDelta` messages to `{AGENT_REDIS_KEY_PREFIX}:session:{session_id}:deltas`.
- Each `LiveDelta` includes `final_event_id`, allowing clients to reconcile live updates with the persisted final UIEvent.
- The SSE endpoint subscribes to both `events` and `deltas` channels after replaying persisted UIEvents.
- Validation:
  - `uv run pytest`: 52 passed.
  - `uv run pyright`: 0 errors.
  - `uv run python -m compileall app core scripts`: passed.
  - `ENV=production LOG_LEVEL=WARNING uv run python -u scripts/validate_agent_phase0.py`: passed against remote Postgres/Redis.

## 9. Phase 4: AgentMemory

Source: v4 §12.

- [x] Define `AgentMemory`.
- [x] Define `MemoryPolicy`.
- [x] Add session-scoped memory repository.
- [x] Add compaction/summary service.
- [x] Add memory tests for windowing, summary, schema_version, and safety flags.

M1 only:

- Session memory and summarization.
- LongTermMemory interface placeholder only.

Current implementation snapshot, 2026-07-08:

- `AgentMemory`, `MemorySourceRef`, `MemoryScope`, `MemoryKind`, `MemoryWindow`, `MemoryPolicy`, and `WindowMemoryPolicy` live in `app/domain/agent/memory.py`.
- `AgentMemoryService` builds a session memory window through a policy and repository.
- `InMemoryAgentMemoryRepository` is the M1 in-process repository used by tests; no `agent_memories` table is introduced yet.
- `tests/test_agent_memory.py` covers windowing, summaries, source/provenance, confidence, scope, sensitive filtering, schema_version, and keeping long-term memory out of the M1 session window.
- Validation:
  - `uv run pytest`: 17 passed.
  - `uv run pyright`: 0 errors.

## 9.1 Phase 4A: Lightweight AgentProfile and Effective Config

Source: v4 §0.1, §8, §20, §24.1, §31, §35.

This is M1 scope. It is not multi-agent orchestration and not AgentRegistry.
The goal is that one graph runtime can be concretized for different
business agent shapes without forking graph code.

- [x] Define `AgentProfile` and `EffectiveAgentConfig` domain/config models.
- [x] Add a small built-in profile catalog, code or YAML backed.
- [x] Keep `agent_profile_key` on `CreateRunCommand` and `agent_runs`.
- [x] Record `agent_profile_version` on runs.
- [x] Resolve profile at run start into prompt/model/tools/permissions/memory policy/ragflow binding.
- [x] Add tests that two profiles select different effective config while using the same graph.
- [x] Add permission test proving a profile can deny a tool.

Hard rules:

- M1 AgentProfile switching does not mean Supervisor, handoff, or multiple agents.
- Do not add AgentRegistry or GraphRegistry in M1.
- Do not let business code fork graph implementation per profile.
- Profile resolution must happen before graph execution and be passed as effective config.

Current implementation snapshot, 2026-07-08:

- `AgentProfile`, `EffectiveAgentConfig`, and `MemoryPolicyConfig` live in `app/domain/agent/profiles.py`.
- `build_builtin_profile_catalog()` provides `default_research` and `literature_review`.
- `AgentRunService.create_run()` resolves the requested profile before persisting the run.
- `agent_profile_key` and `agent_profile_version` are returned by the create-run API path.
- `tests/test_agent_profile.py` covers profile switching, unknown-profile rejection, version persistence, and tool deny behavior.
- Validation:
  - `uv run pytest`: 10 passed.
  - `uv run pyright`: 0 errors.
  - `ENV=production LOG_LEVEL=WARNING uv run python -u scripts/validate_agent_phase0.py`: passed against remote Postgres/Redis.

## 10. Phase 5: First M1 Graph Runtime

Source: v4 §14, §15, §16, §19.

- [x] Define `BaseAgentState` for orchestration-independent execution fields.
- [x] Define the first graph-specific state, e.g. `PlannerReactState`, only for fields specific to that graph shape.
- [x] Add idempotent reducers.
- [x] Add `GraphRuntimeService` facade.
- [x] Add the first M1 graph. Planner-ReAct may be the first concrete shape, but generic runtime code must not be named after it.
- [x] Add `RunBudget`.
- [x] Add `AgentError`.
- [x] Add `ToolResultHandlerRegistry`.
- [x] Add default handler and first tool handler.
- [x] Add `ToolExecutionPort`.
- [x] Add `LLMPort` and one provider/fake adapter.
- [x] Add tool permissions.
- [x] Add tool side-effect idempotency by `tool_call_id`.
- [x] Add tests for reducer idempotency.

Hard rules:

- `BaseAgentState` owns common fields such as ids, messages, tool results, artifacts, status, budget, and lineage.
- Graph-specific state owns only orchestration-specific fields such as plan/current step.
- Changing graph shape later must add a new graph-specific state, not rename or overload the base state.
- Nodes return state patches.
- Side effects go through ports/services/sinks.
- Runner must not branch with `if/elif` by `tool_name`.
- New tools add ToolResultHandlers, not runner branches.

Current implementation snapshot, 2026-07-08:

- `BaseAgentState`, `PlannerReactState`, `ArtifactRef`, `merge_versioned_dict`, `append_unique_by_id`, and `validate_base_state_layering` live in `app/infrastructure/graph/state.py`.
- Common state uses references for artifacts and memory, not object bodies or memory dumps.
- `tests/test_agent_graph_state.py` covers stale-plan rejection, replay-idempotent append, and State no-mixing checks.
- `TimelineProjector` now uses event-type handlers with a default fallback instead of a hard-coded map-only flow.
- `ToolExecutionResult`, `ArtifactRef`, `ToolResultProjection`, `ToolExecutionPort`, and `ToolResultHandler` live in `app/domain/agent/tools.py`.
- `ToolResultHandlerRegistry` lives in `app/application/agent/tool_result_handlers.py` with default and file handlers.
- Tool handlers project tool output into LLM-visible `StoredMessage`, `ArtifactRef`, and `DomainEvent` without runner `tool_name` branches.
- `LLMPort`, `LLMContext`, `LLMRequest`, `LLMResponse`, and `EvidenceRef` live in `app/domain/agent/llm.py`.
- `DeterministicFakeLLM` lives in `app/infrastructure/agent/fake_llm.py` and provides a no-network provider for tests/golden cases.
- `SandboxPort`, `SandboxRequest`, and `SandboxResult` live in `app/domain/agent/sandbox.py`.
- `DeterministicFakeSandbox` lives in `app/infrastructure/agent/fake_sandbox.py`; it never executes a real shell.
- AgentProfile permission tests cover sandbox/tool allow-deny behavior.
- `RunBudget`, `AgentError`, and `AgentErrorCode` live in `app/domain/agent/runtime.py`.
- Budget tests cover immutable usage updates and structured `budget_exceeded` errors.
- `GraphRuntimeService` lives in `app/application/agent/graph_runtime_service.py` and owns generic config/stream result handling without importing LangGraph.
- `LangGraphRuntimeService` lives in `app/infrastructure/graph/langgraph_runtime.py` and translates resume input into LangGraph `Command`.
- `ToolSideEffectService` lives in `app/application/agent/side_effect_service.py` and records side effects once by `tool_call_id`.
- `AgentRepository.record_side_effect_once()` is the Postgres-backed store for the existing `tool_side_effects` table.
- `build_first_m1_graph()` lives in `app/infrastructure/graph/first_m1_graph.py`; the file/builder use neutral first-graph naming while graph-specific state remains `PlannerReactState`.
- First M1 graph tests cover input normalization, plan creation/update, assistant summary output, structured budget error, and no-mixing rejection at node boundary.
- `tests/golden/first_m1_graph.json` is the first deterministic golden case for the M1 graph skeleton.
- Validation:
  - `uv run pytest`: 52 passed.
  - `uv run pyright`: 0 errors.
  - `uv run python scripts/run_agent_golden.py --fixture tests/golden/first_m1_graph.json`: passed.
  - `ENV=production LOG_LEVEL=WARNING uv run python -u scripts/validate_agent_phase0.py`: passed against remote Postgres/Redis.

## 11. Phase 6: Durable HITL

Source: v4 §17.

- [x] Implement ask_user interrupt.
- [x] Emit `HumanInputRequested`.
- [x] Project to `TimelineWaitInputDisplayed`.
- [x] Resume with `ResumeRunCommand`.
- [x] Validate resume token and session state.

Acceptance:

- Waiting tasks survive process restart.
- Resume continues from interrupt point.

Current implementation snapshot, 2026-07-08:

- The Phase 0 graph uses LangGraph interrupt to stop at ask-user and persists `status='waiting'` plus `resume_token`.
- `HumanInputRequested` projects to `TimelineWaitInputDisplayed`.
- `AgentRunService.resume_run()` rejects missing runs, non-waiting runs, missing stored resume tokens, and token mismatches before dispatching the resume command.
- Resume still runs through the Phase 0 Celery graph task and LangGraph `Command(resume=...)` adapter.
- Validation:
  - `uv run pytest`: 52 passed.
  - `uv run pyright`: 0 errors.
  - Real Celery worker restart validation now passes through `scripts/validate_deployed_agent_worker_restart.py`.

## 12. Phase 7: M1 Release Gate

- [x] Single graph passes golden set.
- [x] Old project golden samples are compared as behavior reference.
- [x] Phase 0 checkpoint/SSE/replay acceptance still passes.
- [x] Deployment templates support the M1 runtime:
  - API deployment has required config/env for agent sessions, Postgres checkpoint/event tables, Redis/SSE, and Celery producer.
  - `celeryworker-research-admin-backend` runs the same backend image and starts the graph-runner Celery worker.
  - The application reads `CELERY_BROKER_URL`; k8s injects producer credentials for API and worker credentials for Worker.
  - ConfigMap/Secret additions are generated through the existing k8s template/generate flow, not hard-coded in application code.
- [x] `deploy-research-app-all.sh validate-resources --cluster KIND` or equivalent dry validation succeeds.
- [x] A controlled KIND deployment verifies pods, logs, API health, Celery worker startup, and the Phase 0/M1 validation flow against deployed services.
- [x] No user traffic is routed until the golden set passes.
- [x] Required ADRs exist:
  - ADR-001, ADR-002, ADR-003
  - ADR-009, ADR-010
  - ADR-013, ADR-015, ADR-016
  - ADR-019, ADR-021, ADR-022
  - ADR-023, ADR-024, ADR-025, ADR-026, ADR-027

Acceptance:

- M1 is demoable, recoverable, measurable, deployable, and protected by tests.

Current implementation snapshot, 2026-07-09:

- `tests/golden/old_project_planner_react_reference.json` cites old read-only source files under `/home/zymun/imooc/imooc-mas/mooc-manus`.
- The old-project reference checks behavior only: current graph must create a plan, include at least one executable step, complete a step, and emit an assistant-facing output.
- `scripts/agent_golden.py` handles this as `old_project_behavior_reference`; it does not import, copy, or fallback to the old Planner-ReAct code.
- Remote Phase 0 validation passed again on 2026-07-09 after the Redis ACL user was re-upserted and the session lock path was confirmed.
- Controlled KIND deployment passed on 2026-07-09 with temporary image `harbor.sunmoonai.com:30443/app-images/research-admin-backend:codex-1-v4-20260709-5`; the clean target tag is `harbor.sunmoonai.com:30443/app-images/research-admin-backend:1.0.1`.
- Harbor now retains `research-admin-backend:1.0.1` as the clean target tag with digest `sha256:2db6d53e7a6560cda6d08e518b1e472fbbac9b2661a1233a09957f22e17c3f45`; temporary `codex-1-v4-20260709*` tags were removed from `app-images/research-admin-backend`.
- The `1.0.1` image was rebuilt from the current codex-1 source and passed an image-level import check for `app.main`, `app.tasks.agent_graph`, agent routes, Alembic, default traffic gate, and Celery queue.
- Deployed validation passed through API -> Celery -> LangGraph -> Postgres events/checkpoint -> Redis/SSE:
  - session_id `3e0ade1f-63e2-4952-8136-8a8964059ea8`
  - run_id `74cbe3e3-ab5c-42cd-8bbc-7e2c36b04307`
  - timeline `TimelineRunStarted`, `TimelineWaitInputDisplayed`, `TimelineUserInputReceived`, `TimelineToolStarted`, `TimelineToolCompleted`, `TimelineRunCompleted`
  - HTTP replay and SSE replay both returned the cursor-tail events.
- Deployed worker restart validation passed through API -> Celery restart -> resume -> LangGraph -> Postgres events/checkpoint -> Redis/SSE:
  - session_id `f8f05926-a98a-434e-886d-4f9f33c346ef`
  - run_id `767dcc2d-3a06-4535-9dcf-d1e3dfe4667f`
  - `kubectl rollout restart deployment/celeryworker-research-admin-backend -n app-platform-dev`
  - resume completed with the same expected timeline; HTTP replay and SSE replay both returned cursor-tail events.
- Final deployed `research-admin-backend` and `celeryworker-research-admin-backend` images are `harbor.sunmoonai.com:30443/app-images/research-admin-backend:1.0.1`.
- Final deployed `AGENT_V4_TRAFFIC_ENABLED=false`; POST `/api/agent/sessions` returns `404` while the gate is closed.
- Required M1 ADRs now exist: ADR-001, ADR-002, ADR-003, ADR-009, ADR-010, ADR-013, ADR-015, ADR-016, ADR-019, ADR-021, ADR-022, ADR-023, ADR-024, ADR-025, ADR-026, and ADR-027.
- k8s `research-admin-backend` ConfigMap/Secret templates now expose M1 runtime variables for session TTL, Redis session lock TTL, v4 traffic flag, Celery queue, Celery broker/result backend, frontend URL, and Casdoor settings.
- Agent v4 API routes are guarded by `AGENT_V4_TRAFFIC_ENABLED`; the backend default and `.env.example` default are `false`, so traffic stays closed unless deployment explicitly enables the flag.
- Backend `Settings.celery_queue` and k8s worker ConfigMap default to `research.admin.default`.
- Local k8s YAML generation wrote the expected ConfigMap/Secret output.
- `KUBECONFIG=$HOME/.kube/kind-config ./deploy-research-app-all.sh validate-resources --cluster KIND` passed on 2026-07-09 when run outside the sandbox. The earlier sandboxed run was blocked by kubectl connectivity, not by k8s templates.
- Validation:
  - `uv run pytest`: 54 passed.
  - `uv run pyright`: 0 errors.
  - `uv run python -m compileall app core scripts`: passed.

## 12.1 Phase 8: Deployment Closure

This phase is intentionally late: do not start by reshaping k8s before the
runtime contract exists. Once Phase 0 proves the skeleton and Phase 7 is close,
sync deployment the way `info-app` does: source work and platform work are both
tracked, and the handoff records exactly what was deployed or deferred.

Scope:

- [x] Update `/home/zym/k8s/sunmoonai/app-platform/research-app` only for real runtime needs discovered by M1.
- [x] Ensure `research-admin-backend` image contains the LangGraph runtime, API endpoints, Alembic migrations, and Celery tasks.
- [x] Ensure `celeryworker-research-admin-backend` starts the correct worker command/queue for graph execution.
- [x] Add or update ConfigMap/Secret templates for M1-only variables:
  - checkpoint/event database URL or existing database secret wiring
  - Redis URL/session lock/SSE settings
  - Celery broker URL injection
  - feature flag to keep v4 traffic disabled until release gate passes
  - model/tool provider credentials only when the first M1 graph runtime phase needs them
- [x] Keep `nodebullworker-research-web-backend` out of LangGraph execution.
- [x] Record image tags, deploy command, cluster, validation commands, and known deploy gaps in `docs/mooc-manus-v4-handoff.md` and k8s-side docs.

K8S platform docs:

- `/home/zymun/master/k8s/sunmoonai/app-platform/research-app/docs/research-app-moocmanus-v4-deployment.md`
- `/home/zymun/master/k8s/sunmoonai/app-platform/research-app/docs/research-app-moocmanus-v4-deployment-tasks.md`

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

- Keep backend runtime changes scoped to `research-admin-backend` until a phase explicitly needs frontend or k8s. Phase 7/8 explicitly includes k8s deployment closure.
- Frontend work is allowed only when the slice explicitly targets the Agent UI contract. In M1, Agent UI work belongs to `research-web-frontend` and consumes `/api/agent/**` directly.
- Do not copy old MoocManus source.
- Do not copy old `mooc-manus/ui` data consumption logic; use it only for shell/interaction golden samples.
- Do not add a `state` table.
- Do not add `workspaces` or `projects` tables in M1. `project_id`, if needed, is a placeholder field only.
- Do not put TransportMessage in M1 domain.
- Do not implement GraphRegistry in M1.
- Do not route graph control through events.
- Do not expose raw LangGraph events to frontend consumers.
- Do not expose `/api/admin/**` to end-user Agent UI flows.
- Do not route Agent run/start/resume/SSE through `research-web-backend` in M1.
- Do not write long-term memory automatically before its M2 governance exists.
- Do not connect user traffic before the M1 release gate passes.

## 15.1 Context / State / Memory / Workspace / Storage Layer Gate

Source: v4 §4.2, §9.6.1, §12.1, §26.1, ADR-029.

Decision record: `docs/adr/ADR-029-context-state-memory-workspace-storage-layers.md`.

Every new model/table/port/test must identify which layer it belongs to:

```text
Context: temporary LLM input package; not a source of truth.
State: graph execution state; persisted only through checkpointer snapshots.
Memory: recallable experience/fact/summary with source, confidence, TTL/sensitivity, and scope.
Workspace/Project: M2 product organization layer; M1 does not create the entity or table.
Database: structured facts, metadata, event stream, permissions, indexes, references.
Object Storage: large objects such as files, screenshots, exports, tool artifacts, long logs.
```

M1 implementation checks:

- [x] AgentState tests prove state contains small execution facts/references, not file bodies, full event history, or long-term memory dumps.
- [x] Memory tests prove memory entries carry source/provenance, confidence, scope, and safety flags.
- [x] Object-ref tests prove tool artifact fields are URI/ref/hash/metadata, not large object bodies.
- [x] Context assembly tests prove Context is not persisted as a durable truth source.
- [x] Workspace/Project remains M2: no `workspaces`/`projects` table or product entity in M1.

## 15.2 Strategy / Visitor / Handler Registry Gate

Source: v4 §26.2 and ADR-030.

Decision record: `docs/adr/ADR-030-strategy-visitor-handler-registry.md`.

Use classic patterns only at real variation points. Prefer Protocol + registry +
small handlers over inheritance-heavy designs.

M1 variation points:

- [x] `ToolResultHandlerRegistry`: tool result -> LLM-visible content, artifact refs, DomainEvent, UI card.
- [x] `TimelineProjector`: DomainEvent -> UIEvent through event-type handlers/visitors.
- [x] `MemoryPolicy`: windowing, summary, long-term write candidate, recall filter.
- [x] `AgentProfile` / `EffectiveAgentConfig`: prompt, tools, permissions, model, memory policy, ragflow binding.
- [x] `LLMPort`: provider adapter boundary; M1 may use one provider or deterministic fake.
- [x] `SandboxPort`: local/dev or K8s Pod adapter boundary.

M2-reserved variation points:

- `EvidenceAssembler` sub-strategies for RAG/memory/file/artifact evidence packaging.
- Multi-provider fallback/routing.
- AgentRegistry/GraphRegistry versioned rollout.

Hard rules:

- graph runner does not branch by `tool_name` or `event_type`.
- New tools add a handler; they do not modify the runner main flow.
- New UI projections add projector handlers; they do not modify EventSink.
- New memory behavior adds MemoryPolicy; it does not mutate AgentMemory into a strategy holder.
- Registries need default/fallback handlers and structured errors for unknown types.

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

Do not skip ahead to M2 or the full first M1 graph runtime before Phase 0 is
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
3. Validate the M1 Agent UI slice now added in `research-web-frontend`:
   - it calls `/api/agent/**` on `research-admin-backend`;
   - it renders the persisted UIEvent timeline;
   - it renders LiveDelta as live feedback only;
   - it supports interrupt/resume with `resume_token`;
   - it keeps Node web-backend out of the Agent path.
4. Build/push `research-web-frontend:1.0.1`, deploy it through k8s, and verify the page can reach the FastAPI ingress configured by `NEXT_PUBLIC_API_URL`.
5. Keep the traffic gate closed until the broader product golden set is approved for user traffic.
6. Revisit the M2 roadmap item by item with evidence from real usage.
