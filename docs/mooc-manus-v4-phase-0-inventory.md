# MoocManus v4 Phase 0 Inventory

Status: draft
Date: 2026-07-06
Scope: Phase -1 boundary check and Phase 0 Walking Skeleton preparation.

## 1. v4 Constraints Read

The v4 plan has been read end-to-end before implementation.

Strict constraints that affect Phase 0:

- Walking Skeleton comes before semantic freeze and full domain modeling.
- Old MoocManus is read-only reference and golden sample source.
- Long agent work must not run inside HTTP request lifecycle.
- Python graph execution belongs in the Celery worker.
- Checkpoint is runtime state persistence, not memory.
- DomainEvent is append-only truth.
- UIEvent is rebuildable projection.
- Redis Pub/Sub/SSE is transport only, not truth source.
- TransportMessage envelope is M2 and must not be added to M1 domain.
- Graph routing must use state/edge/Command/interrupt, not events.
- Tool side effects must be idempotent under checkpoint replay.

## 2. Current Target Source State

Target source root:

```text
/home/zym/research-app/research-admin-backend/app
```

Existing platform shell to preserve:

```text
app/main.py
app/worker.py
core/config.py
app/infrastructure/storage/postgres.py
app/infrastructure/storage/redis.py
app/infrastructure/messaging/celery_producer.py
app/infrastructure/logging/logging.py
app/interfaces/errors/exception_handlers.py
app/interfaces/endpoints/routes.py
app/interfaces/endpoints/auth_routes.py
app/interfaces/endpoints/tasks_routes.py
app/tasks/ping.py
alembic/
pyproject.toml
Dockerfile
```

Currently empty or placeholder packages where new MoocManus code can grow:

```text
app/domain/models/
app/domain/repositories/
app/domain/services/
app/application/services/
app/infrastructure/repositories/
```

Recommended new packages for Phase 0:

```text
app/agent_runtime/
app/domain/models/
app/domain/ports/
app/application/services/
app/infrastructure/graph/
app/infrastructure/repositories/
app/interfaces/endpoints/
app/tasks/
```

## 3. Existing Dependencies

Current runtime dependencies:

```text
alembic
asyncpg
fastapi
httpx
pydantic
pydantic-settings
redis
celery
sqlalchemy
uvicorn[standard]
```

Missing for Phase 0:

```text
langgraph
langchain-core
Postgres checkpointer package or local CheckpointPort adapter decision
```

Open dependency question:

```text
Use LangGraph's official Postgres checkpointer package if available in the environment;
otherwise create a temporary M1 CheckpointPort wrapper only for the skeleton, then replace
with the official checkpointer before the M1 release gate.
```

## 4. Existing Runtime Hooks

FastAPI lifecycle already initializes:

```text
Redis
Postgres
Celery producer
```

Celery worker already exists:

```text
app/worker.py
```

Registered task today:

```text
app.tasks.ping
```

Existing internal enqueue endpoint:

```text
POST /api/internal/tasks/ping
```

Phase 0 can reuse these hooks, but agent graph execution must use a new task module rather than overloading ping.

## 5. Phase 0 Minimal Surface

The first implementation should create the smallest useful vertical slice:

```text
POST /api/agent/sessions
POST /api/agent/sessions/{session_id}/runs
POST /api/agent/runs/{run_id}/resume
GET  /api/agent/sessions/{session_id}/events
GET  /api/agent/sessions/{session_id}/stream
```

The graph itself should stay tiny:

```text
START
  -> ask_user_node
  -> side_effect_tool_node
  -> END
```

The side effect can be intentionally simple:

```text
write one idempotency marker row or file using tool_call_id
```

This should be enough to verify checkpoint replay does not repeat the side effect.

## 6. Minimal Tables to Design

Use v4 target schema names from the start where possible:

```text
sessions
agent_runs
session_events
```

Phase 0 may add one skeleton-only idempotency table if needed:

```text
tool_side_effects(tool_call_id, run_id, result, created_at)
```

Do not add:

```text
state table
TransportMessage table
CQRS materialized timeline table
GraphRegistry tables
long_term_memories implementation tables unless required by existing migrations
```

## 7. Phase 0 Acceptance Script

The validation script must cover v4 §6.5.3:

```text
1. Trigger graph -> ask_user -> interrupt -> session.status=waiting.
2. Kill/restart worker -> resume by thread_id without starting over.
3. Submit user input -> resume -> side effect runs once.
4. Force replay after side effect -> side effect is not repeated.
5. Disconnect SSE -> reconnect with last_event_id -> no missing/duplicate timeline.
```

This script becomes the first golden case in Phase 0.5.

## 8. First Implementation Order

1. Add dependencies and confirm importability.
2. Add minimal Phase 0 schema/migration.
3. Add minimal event append/query path.
4. Add minimal SSE path with `last_event_id`.
5. Add two-node LangGraph.
6. Add Celery graph-runner task.
7. Add resume path.
8. Add side-effect idempotency cache.
9. Add validation script/test.

## 9. Guardrails

- Do not copy old MoocManus files into the target source.
- Do not implement full Planner-ReAct before Phase 0 acceptance passes.
- Do not introduce TransportMessage in M1.
- Do not route graph control through DomainEvent/UIEvent.
- Do not build UI polish during Phase 0.
- Do not connect user traffic until golden set passes.
