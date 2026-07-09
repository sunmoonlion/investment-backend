from __future__ import annotations

from app.application.agent.run_logging import lineage_log_extra
from app.domain.agent.models import RunLineage


def test_lineage_log_extra_includes_run_lineage_fields() -> None:
    lineage = RunLineage(
        session_id="session-1",
        run_id="run-1",
        root_run_id="root-1",
        parent_run_id="parent-1",
    )

    assert lineage_log_extra(lineage, phase="phase0") == {
        "session_id": "session-1",
        "run_id": "run-1",
        "root_run_id": "root-1",
        "parent_run_id": "parent-1",
        "phase": "phase0",
    }
