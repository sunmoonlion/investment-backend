from __future__ import annotations

from app.worker import celery_app


@celery_app.task(name="app.tasks.pilot_agent_graph.run")
def run_pilot_agent_graph(run_id: str, resume: str | None = None) -> None:
    raise RuntimeError(
        "Direct pilot dispatch is disabled; use the transactional Agent outbox"
    )
