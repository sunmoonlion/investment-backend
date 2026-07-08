# MoocManus v4 Handoff

Date: 2026-07-08
Status: Phase 0 implementation started

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
code plus refreshed handoff/task documentation.

Resume rule:

1. Sync `research-app` parent to `codex-1`.
2. Sync `research-admin-backend` to `codex-1`.
3. Keep other research-app subrepos on `master` unless a phase explicitly needs them.
4. Keep `/home/zym/k8s` or `/home/zymun/k8s` on the active work branch only when deployment changes are required.
5. Read this handoff first, then read `docs/mooc-manus-v4-rebuild-task.md`.
6. Start with Phase 0 Walking Skeleton.

## 1. Overall State

MoocManus v4 Phase 0 application code has started.

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
- Redis ACL user `research_admin_backend` was recreated/updated with `agent:*` key/channel access.
- Postgres checkpoint resume across separate DB/checkpointer connections passed.
- Remote Postgres/Redis Phase 0 integration flow passed through waiting/resume/completed.
- `scripts/validate_agent_phase0.py` is the repeatable Phase 0 validation entrypoint.
- HTTP `/api/agent/sessions/{session_id}/events` full replay and cursor replay are covered by the Phase 0 validation script.
- SSE endpoint replays persisted UIEvents after `last_event_id` before subscribing to Redis.
- Unit tests cover interrupt/resume and TimelineProjector.

Not done yet:

- Real Celery process kill/restart validation is not scripted yet. The underlying Postgres checkpoint recovery prerequisite has passed across separate connections.
- Real network-level SSE disconnect/reconnect validation is not scripted yet. HTTP `/events` full replay and `after_event_id` replay are covered by the Phase 0 validation script; the SSE endpoint's replay-before-subscribe path exists.
- No golden harness exists.
- No Planner-ReAct graph exists.
- No k8s deployment changes for MoocManus v4 have been made yet.

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

Do not import, deploy, copy, or fallback to the old Planner-ReAct flow.

## 4. Strict v4 Order

Do not start by building the full domain model or full Planner-ReAct graph.
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
- Do not implement full Planner-ReAct before Phase 0 passes.
- Do not introduce `TransportMessage` in M1.
- Do not route graph control with DomainEvent/UIEvent.
- Do not run long agent work inside the FastAPI request lifecycle.
- Do not connect user traffic until golden set passes.

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
