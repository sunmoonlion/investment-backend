# MoocManus v4 Handoff

Status: ready for transfer
Date: 2026-07-06
Repo: `/home/zym/research-app/research-admin-backend`

## 1. What Was Done

The v4 architecture plan was read end-to-end:

```text
/home/zym/k8s/sunmoonai/docs/mooc-manus-langgraph-longterm-plan-v4.md
```

Two planning documents were added:

```text
docs/mooc-manus-v4-rebuild-task.md
docs/mooc-manus-v4-phase-0-inventory.md
```

No application code was changed.

Current git state in `research-admin-backend` should be:

```text
?? docs/mooc-manus-v4-phase-0-inventory.md
?? docs/mooc-manus-v4-rebuild-task.md
?? docs/mooc-manus-v4-handoff.md
```

Note: running `uv run ...` created a local `.venv/` under `app/`, but it is ignored and not part of the handoff.

## 2. Core Decision

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

## 3. Strict v4 Order

The most important correction is ordering.

Do not start by building the full domain model or full Planner-ReAct graph.

v4 requires:

```text
Phase -1: App Platform boundary
Phase 0:  Walking Skeleton
Phase 0.5: Minimal evaluation/golden harness
Phase 1:  Semantic freeze and message boundary
Phase 2:  EventSink and projection
Phase 3+: Larger M1 platform
```

Phase 0 must validate physical assumptions before platform buildout:

```text
checkpoint + interrupt + resume
worker restart resume by thread_id=session_id
SSE reconnect by last_event_id
tool side-effect idempotency under replay
```

## 4. Current Source State

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

## 5. Dependency State

Current dependencies include FastAPI, SQLAlchemy, asyncpg, Redis, Celery, Alembic, Pydantic.

Missing for Phase 0:

```text
langgraph
langchain-core
langgraph-checkpoint-postgres
```

The previous machine could not sync dependencies because network/DNS access failed:

```text
uv run python -c "import langgraph, langchain_core; print('ok')"
```

It attempted to download packages and failed on DNS resolution. A later escalated `uv sync` request was rejected. On the next machine, do this first:

```bash
cd /home/zym/research-app/research-admin-backend/app
uv sync
uv add langgraph langchain-core langgraph-checkpoint-postgres
uv run python -c "import langgraph, langchain_core; from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver; print('ok')"
```

If the project does not want `uv add` to mutate the lock before review, manually edit `pyproject.toml` first, then run `uv lock`.

## 6. Phase 0 Implementation Target

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

## 7. Phase 0 Acceptance

The validation script/test must prove:

```text
1. Trigger graph -> ask_user -> interrupt -> session.status=waiting.
2. Kill/restart worker -> resume by thread_id without starting over.
3. Submit user input -> resume -> side effect runs once.
4. Force replay after side effect -> side effect is not repeated.
5. Disconnect SSE -> reconnect with last_event_id -> no missing/duplicate timeline.
```

This script becomes the first golden case in Phase 0.5.

## 8. Guardrails

- Do not copy old MoocManus files into the new source tree.
- Do not implement full Planner-ReAct before Phase 0 passes.
- Do not introduce `TransportMessage` in M1.
- Do not route graph control with DomainEvent/UIEvent.
- Do not run long agent work inside the FastAPI request lifecycle.
- Do not connect user traffic until golden set passes.

## 9. Suggested Next Commit

Commit the docs first:

```bash
cd /home/zym/research-app/research-admin-backend
git add docs/mooc-manus-v4-rebuild-task.md docs/mooc-manus-v4-phase-0-inventory.md docs/mooc-manus-v4-handoff.md
git commit -m "docs: add mooc manus v4 rebuild handoff"
```

Then begin Phase 0 in a separate commit:

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
