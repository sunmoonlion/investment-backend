from __future__ import annotations

from typing import Any

from app.domain.agent.models import RunLineage


def lineage_log_extra(lineage: RunLineage, **extra: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "session_id": lineage.session_id,
        "run_id": lineage.run_id,
        "root_run_id": lineage.root_run_id,
        "parent_run_id": lineage.parent_run_id,
    }
    fields.update(extra)
    return fields
