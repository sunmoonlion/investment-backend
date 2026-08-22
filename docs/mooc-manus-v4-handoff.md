# MoocManus v4 Handoff

Date: 2026-07-08
Status: Phase 0 / 0.5 implemented, Phase 1 started, task backlog resynced with latest v4 plan

## 0. Pause Snapshot

This handoff follows the `info-app` style: it records the current branch heads,
what is complete, what is not complete, how to resume, and how to validate.

Current branch heads at the time of this handoff:

```text
research-app parent          add5bcf  codex-1
research-admin-backend       7c75d32  codex-1
research-admin-frontend      3ef205a  master
research-web-backend         df6d90b  master
research-web-frontend        dd36bbd  master
k8s                          87bf2e6  master
```

Current local changes include the first Phase 0 Walking Skeleton application
code, the Phase 0.5 golden harness, Phase 1 semantic-boundary code, and refreshed
handoff/task documentation.

Resume rule:

1. Sync `research-app` parent to `codex-1`.
2. Sync `research-admin-backend` to `codex-1`.
3. Keep other research-app subrepos on `master` unless a phase explicitly needs them.
4. Keep `/home/zym/k8s` or `/home/zymun/master/k8s` on the active work branch only when deployment changes are required.
5. Read this handoff first, then read `docs/mooc-manus-v4-rebuild-task.md`.
6. Continue from Phase 1 ADRs or the next smallest Phase 2 task; do not skip the newly resynced M1 constraints.

## 1. Overall State

MoocManus v4 Phase 0 application code has started and the backlog has been
resynced with the latest v4 plan changes made after the master/codex branch
switch.

Completed so far:

- v4 architecture plan was read end-to-end.
- `codex-1` branch exists for the parent `research-app` repo.
- `codex-1` branch exists for `research-admin-backend`.
- Parent `codex-1` records the submodule combination for the Codex work line.
- `research-admin-backend` has the v4 planning docs.
- `docs/mooc-manus-v4-rebuild-task.md` is now the authoritative execution backlog.
- `docs/mooc-manus-v4-phase-0-inventory.md` remains a focused Phase 0 inventory.
- Basic db/redis bootstrap fixes were separated onto `master` and merged into the Codex line.
- `langgraph`, `langchain-core`, and `langgraph-checkpoint-postgres` are recorded in `pyproject.toml` / `uv.lock`.
- Minimal agent domain models and commands exist.
- Minimal Alembic schema exists for `agent_sessions`, `agent_runs`, `session_events`, and `tool_side_effects`.
- Minimal repositories, `DBEventSink`, and `TimelineProjector` exist.
- Minimal FastAPI agent routes exist under `/api/agent`.
- Minimal Celery graph task exists as `app.tasks.agent_graph.run`.
- LangGraph Walking Skeleton exists with `ask_user_node -> side_effect_tool_node`.
- Phase 0 graph runner is wired to LangGraph Postgres checkpointer.
- LangGraph checkpoint tables are included in the Phase 0 Alembic migration.
- Alembic supports optional `MIGRATION_DATABASE_URL` so DDL can use a migration account while runtime uses `DATABASE_URL`.
- Remote Alembic migration was applied with the Postgres admin/migration account.
- Runtime DB grants were applied for `sunmoonai_dev` and `research_admin_user`.
- Redis ACL user `research_admin_backend` was recreated/updated with `research:*` key access and `research:agent:*` channel access.
- Postgres checkpoint resume across separate DB/checkpointer connections passed.
- Remote Postgres/Redis Phase 0 integration flow passed through waiting/resume/completed.
- `scripts/validate_agent_phase0.py` is the repeatable Phase 0 validation entrypoint.
- HTTP `/api/agent/sessions/{session_id}/events` full replay and cursor replay are covered by the Phase 0 validation script.
- SSE endpoint replays persisted UIEvents after `last_event_id` before subscribing to Redis.
- Phase 0.5 has the first deterministic golden case in `tests/golden/phase0_walking_skeleton.json`.
- The first M1 graph skeleton has a deterministic golden case in `tests/golden/first_m1_graph.json`.
- The old Planner-ReAct behavior is represented by `tests/golden/old_project_planner_react_reference.json` as a read-only behavior reference.
- `scripts/run_agent_golden.py` runs golden fixtures without Postgres, Redis, Celery, or live LLM calls.
- Phase 1 semantic boundary has started: `StoredMessage`, `MessageRole`, `CancelRunCommand`, stored-message upcaster, and LangChain message mapper exist.
- Import-boundary tests ensure domain agent code does not import LangGraph/LangChain and application agent code does not import LangGraph.
- `docs/mooc-manus-v4-rebuild-task.md` now reflects the latest v4 additions:
  M1 lightweight AgentProfile / EffectiveAgentConfig switching,
  Context / State / Memory / Workspace / Database / Object Storage layer gates,
  and Strategy / Visitor / Handler Registry implementation constraints.
- Required M1 ADRs now exist under `docs/adr/`: ADR-001, ADR-002, ADR-003, ADR-009, ADR-010, ADR-013, ADR-015, ADR-016, ADR-019, ADR-021, ADR-022, ADR-023, ADR-024, ADR-025, ADR-026, and ADR-027. ADR-029 and ADR-030 record the later v4 refinements for storage boundaries and Strategy/Visitor/Handler Registry constraints.
- Lightweight M1 AgentProfile / EffectiveAgentConfig switching is implemented with built-in `default_research` and `literature_review` profiles.
- Create-run resolves profile key/version before persisting the run and returns the selected profile identity.
- `BaseAgentState` and `PlannerReactState` exist under `app/infrastructure/graph/state.py`; generic state is not named after the first concrete graph shape.
- State reducers cover stale-version rejection and replay-idempotent append.
- `TimelineProjector` now uses event-type handlers with a default fallback.
- M1 `AgentMemory` / `MemoryPolicy` exist with a session-window service and in-memory repository; no memory table has been introduced yet.
- `ToolResultHandlerRegistry` exists with default and file handlers; tool results project into LLM `StoredMessage`, artifact refs, and DomainEvent without runner tool-name branching.
- `LLMPort` exists with `LLMContext` and `DeterministicFakeLLM`; tests/golden can stay offline.
- `SandboxPort` exists with `DeterministicFakeSandbox`; it does not execute real shell commands.
- `RunBudget` and `AgentError` exist with structured `budget_exceeded` and run-error serialization.
- `GraphRuntimeService` exists as an application facade without LangGraph imports; `LangGraphRuntimeService` is the infrastructure adapter for LangGraph `Command` resume.
- `ToolSideEffectService` wraps side-effect idempotency by `tool_call_id`; Phase 0 forced replay still leaves one side-effect row.
- `RedisSessionLock` protects graph execution by `session_id` with `AGENT_SESSION_LOCK_TTL_SECONDS`; remote Phase 0 validation passes with the lock enabled.
- `RedisSessionLock.renew()` extends the TTL only for the current owner token; graph worker renews before and after graph execution.
- Lock release uses Redis `GET`/`DEL` instead of Lua `EVAL` because the runtime Redis ACL currently disallows `EVAL`.
- On 2026-07-09, the `research_admin_backend` Redis ACL user was re-upserted after an authentication mismatch, and remote Phase 0 validation passed again.
- ADR-026 records the Redis/SSE/session-lock decision, including the current no-`EVAL` ACL constraint.
- `SecurityContext` exists as a fixed single-tenant M1 placeholder and is carried by create/resume/cancel commands through the Celery graph task boundary.
- Graph worker logs use `lineage_log_extra()` so loaded/start/waiting/completed/failed/lock-busy records carry `RunLineage` fields.
- M1 run state transitions are explicit and enforced by `AgentRepository.set_run_status()` before DB updates.
- `LiveDelta` exists for non-persisted live updates; `DBEventSink` publishes it with `final_event_id` so clients can reconcile with the final persisted UIEvent.
- The SSE endpoint subscribes to both `{AGENT_REDIS_KEY_PREFIX}:session:{session_id}:events` and `{AGENT_REDIS_KEY_PREFIX}:session:{session_id}:deltas` after replaying persisted UIEvents.
- k8s `research-admin-backend` ConfigMap/Secret templates now include M1 runtime env wiring for session TTL, agent session lock TTL, v4 traffic flag, Celery queue, Celery broker/result backend, frontend URL, and Casdoor settings.
- Agent v4 API routes are guarded by `AGENT_V4_TRAFFIC_ENABLED`; source defaults are closed (`false`) and deployment must explicitly enable the flag for controlled validation or release.
- Backend and k8s worker defaults now agree on `CELERY_QUEUE=research.admin.default`.
- Old-project behavior reference is compared without importing or copying old source code.
- `build_first_m1_graph()` exists as a deliberately small M1 graph skeleton with neutral first-graph naming; graph-specific state remains isolated in `PlannerReactState`.
- First M1 graph tests cover input normalization, plan creation/update, assistant summary output, structured budget error, and no-mixing rejection at node boundary.
- `AgentRunService.resume_run()` validates waiting status, stored resume token presence, and token equality before dispatching resume.
- Unit tests cover interrupt/resume, TimelineProjector, AgentProfile, message boundaries, graph state reducers, State no-mixing, Memory no-mixing, tool result handler projection, Context no-mixing, sandbox permissions, budget/error handling, run state transitions, runtime facade behavior, side-effect idempotency, Redis session lock/renewal behavior, LiveDelta/final UIEvent reconciliation, SecurityContext queue-boundary propagation, RunLineage log fields, first M1 graph behavior, golden fixtures, and resume-state guards.

Not done yet:

- Golden harness is present for the Phase 0 walking skeleton, first M1 graph skeleton, and first old-project behavior reference. Broader old-project golden samples are not imported yet.
- Context/State/Memory/Workspace/Storage no-mixing gates have State, Memory, artifact/object-ref, and Context coverage.
- The first M1 graph skeleton exists, but it is not wired into a production route/Celery release path and is not yet a full Planner-ReAct product graph. Planner-ReAct remains only the likely first concrete graph shape, not the platform architecture.
- k8s template changes for MoocManus v4 have a controlled KIND validation record. `validate-resources` passed on 2026-07-09, and API/worker deployed with temporary image `harbor.sunmoonai.com:30443/app-images/research-admin-backend:codex-1-v4-20260709-5`; the clean target tag is `harbor.sunmoonai.com:30443/app-images/research-admin-backend:1.0.1`.
- Harbor cleanup on 2026-07-09 kept rebuilt `research-admin-backend:1.0.1` (`sha256:2db6d53e7a6560cda6d08e518b1e472fbbac9b2661a1233a09957f22e17c3f45`) and removed the temporary `codex-1-v4-20260709*` tags from `app-images/research-admin-backend`.
- The `1.0.1` image import check passed for `app.main`, `app.tasks.agent_graph`, agent routes, Alembic, default traffic gate, and Celery queue.
- Deployed validation passed on 2026-07-09: API -> Celery -> LangGraph -> Postgres events/checkpoint -> Redis/SSE completed the HITL wait/resume flow; HTTP replay and SSE replay returned the expected cursor-tail timeline.
- Deployed worker restart validation passed on 2026-07-09: `scripts/validate_deployed_agent_worker_restart.py` created a waiting run, restarted `celeryworker-research-admin-backend`, resumed the run, and verified the same timeline plus HTTP/SSE replay.
- Final deployed images are `harbor.sunmoonai.com:30443/app-images/research-admin-backend:1.0.1` for both API and worker, and final `AGENT_V4_TRAFFIC_ENABLED=false`; POST `/api/agent/sessions` returns `404` while the traffic gate is closed.
- K8S-side deployment docs now mirror the `info-app` pattern:
  `/home/zymun/master/k8s/sunmoonai/app-platform/research-app/docs/research-app-moocmanus-v4-deployment.md`
  and `/home/zymun/master/k8s/sunmoonai/app-platform/research-app/docs/research-app-moocmanus-v4-deployment-tasks.md`.

## 2. Source Of Truth

Authoritative task/backlog document:

```text
docs/mooc-manus-v4-rebuild-task.md
```

Phase 0 inventory:

```text
docs/mooc-manus-v4-phase-0-inventory.md
```

Original v4 architecture plan:

```text
/home/zym/k8s/sunmoonai/docs/mooc-manus-langgraph-longterm-plan-v4.md
```

Platform deployment templates:

```text
/home/zym/k8s/sunmoonai/app-platform/research-app
```

The rebuild task is the file to update as work progresses. Do not create another
parallel implementation-plan document.

## 3. Core Decision

MoocManus v4 is a greenfield rebuild inside:

```text
/home/zym/research-app/research-admin-backend/app
```

Keep the platform shell:

```text
FastAPI entrypoint
Celery app/worker
Postgres/Redis infrastructure
config/logging/errors
Docker/pyproject/Alembic
existing auth/session-cookie foundation
```

Do not reuse old MoocManus as the engineering base.

Old source is read-only reference and golden sample source only:

```text
/home/zym/imooc-mas/mooc-manus
/home/zym/imooc/imooc-mas/mooc-manus
```

Do not import, deploy, copy, or fallback to the old hand-written Planner-ReAct flow.

## 4. Strict v4 Order

Do not start by building the full domain model or full first M1 graph runtime.
Also do not treat Phase 0 as the final objective.

The objective is to implement the full v4 plan in research-app, in v4 order:

```text
Phase -1: App Platform boundary
Phase 0:  Walking Skeleton
Phase 0.5: Minimal evaluation/golden harness
Phase 1:  Semantic freeze and message boundary
Phase 2:  EventSink and projection
Phase 3+: Larger M1 platform
M2:       Delayed platform capabilities after M1 release gate
```

Phase 0 must validate physical assumptions before platform buildout:

```text
checkpoint + interrupt + resume
worker restart resume by thread_id=session_id
SSE reconnect by last_event_id
tool side-effect idempotency under replay
```

## 5. Current Source State

Target app root:

```text
/home/zym/research-app/research-admin-backend/app
```

Useful existing files:

```text
app/main.py
app/worker.py
core/config.py
app/infrastructure/storage/postgres.py
app/infrastructure/storage/redis.py
app/infrastructure/messaging/celery_producer.py
app/interfaces/endpoints/routes.py
app/interfaces/endpoints/tasks_routes.py
app/tasks/ping.py
pyproject.toml
alembic/
```

Existing packages that are mostly placeholders and can host new code:

```text
app/domain/models/
app/domain/repositories/
app/domain/services/
app/application/services/
app/infrastructure/repositories/
```

Recommended new packages:

```text
app/agent_runtime/
app/domain/ports/
app/infrastructure/graph/
```

## 6. Dependency State

Current dependencies include FastAPI, SQLAlchemy, asyncpg, Redis, Celery,
Alembic, Pydantic.

Recorded for Phase 0:

```text
langgraph
langchain-core
langgraph-checkpoint-postgres
psycopg
psycopg-binary
```

Postgres checkpointer import is confirmed locally. The runtime database account
does not have DDL permission, so do not call `checkpointer.setup()` from the
worker. The Phase 0 Alembic migration owns the checkpoint tables.

Migration command used for remote validation:

```bash
cd /home/zymun/research-app/research-admin-backend/app
MIGRATION_DATABASE_URL='postgresql://<migration_user>:<password>@<host>:<port>/research_admin' \
uv run alembic upgrade head
```

The runtime user must not run migrations because it lacks schema DDL permission.
Keep using `MIGRATION_DATABASE_URL` for future DDL.

Repeatable Phase 0 validation command:

```bash
cd /home/zymun/research-app/research-admin-backend/app
ENV=production LOG_LEVEL=WARNING uv run python -u scripts/validate_agent_phase0.py
```

Repeatable Phase 0.5 golden command:

```bash
cd /home/zymun/research-app/research-admin-backend/app
uv run python scripts/run_agent_golden.py
uv run pytest
```

Current local deterministic test command:

```bash
cd /home/zymun/research-app/research-admin-backend/app
uv run pytest
uv run pyright
uv run python -m compileall app core scripts
```

## 7. Phase 0 Implementation Target

Build the smallest real vertical slice.

Minimal graph:

```text
START
  -> ask_user_node
  -> side_effect_tool_node
  -> END
```

Minimal API surface:

```text
POST /api/agent/sessions
POST /api/agent/sessions/{session_id}/runs
POST /api/agent/runs/{run_id}/resume
GET  /api/agent/sessions/{session_id}/events
GET  /api/agent/sessions/{session_id}/stream
```

Minimal tables:

```text
sessions
agent_runs
session_events
```

Optional Phase 0 idempotency table:

```text
tool_side_effects(tool_call_id, run_id, result, created_at)
```

Do not add:

```text
state table
TransportMessage table
CQRS materialized timeline table
GraphRegistry tables
full long-term memory implementation
```

## 8. Phase 0 Acceptance

The validation script/test must prove:

```text
1. Trigger graph -> ask_user -> interrupt -> session.status=waiting.
2. Kill/restart worker -> resume by thread_id without starting over.
3. Submit user input -> resume -> side effect runs once.
4. Force replay after side effect -> side effect is not repeated.
5. Disconnect SSE -> reconnect with last_event_id -> no missing/duplicate timeline.
```

This script becomes the first golden case in Phase 0.5.

## 9. Guardrails

- Do not copy old MoocManus files into the new source tree.
- Do not implement the full first M1 graph runtime before Phase 0 passes.
- Do not introduce `TransportMessage` in M1.
- Do not route graph control with DomainEvent/UIEvent.
- Do not run long agent work inside the FastAPI request lifecycle.
- Do not connect user traffic until golden set passes.
- Keep `AGENT_V4_TRAFFIC_ENABLED=false` unless running controlled validation or an approved release.

## 10. Resume Checklist

When resuming work in a new session:

1. Check branches:

   ```bash
   cd /home/zym/research-app
   git status -sb
   git submodule status
   ```

2. Check the backend subrepo:

   ```bash
   cd /home/zym/research-app/research-admin-backend
   git status -sb
   ```

3. Check k8s only when the current phase touches deployment:

   ```bash
   cd /home/zym/k8s
   git status -sb
   ```

4. Read the authoritative backlog:

   ```text
   docs/mooc-manus-v4-rebuild-task.md
   ```

5. Implement the next smallest Phase 0 task.

6. After each meaningful step, record:

   - changed files
   - validation command
   - pass/fail result
   - next smallest task
   - deploy impact: none / k8s doc only / k8s template change / deployed to cluster

## 11. Suggested First Implementation Commit

Begin Phase 0 in a separate implementation commit:

```text
1. Add dependencies.
2. Add Phase 0 schema/migration.
3. Add minimal event append/query path.
4. Add SSE with last_event_id.
5. Add two-node graph.
6. Add Celery graph-runner task.
7. Add resume path.
8. Add side-effect idempotency cache.
9. Add validation script/test.
```

After Phase 0 passes, continue through the M1 phases in
`docs/mooc-manus-v4-rebuild-task.md`. Do not stop at the skeleton; use it as the
gate that makes the rest of v4 safe to build.

Deployment is not optional. Phase 7/8 must close the k8s side before user
traffic is enabled:

```text
/home/zym/k8s/sunmoonai/app-platform/research-app/deploy-research-app-all
/home/zym/k8s/sunmoonai/app-platform/research-app/research-admin-backend
/home/zym/k8s/sunmoonai/app-platform/research-app/celeryworker-research-admin-backend
```

The expected validation path follows the existing app-platform style:

```bash
./deploy-research-app-all.sh validate-resources --cluster KIND
./deploy-research-app-all.sh deploy --cluster KIND
./deploy-research-app-all.sh status --cluster KIND
```

## 12. Update Log

| Date | Update |
|---|---|
| 2026-07-06 | Initial v4 planning handoff created. |
| 2026-07-08 | Reworked handoff to match info-app style: branch heads, status, resume checklist, and single authoritative rebuild task. |
