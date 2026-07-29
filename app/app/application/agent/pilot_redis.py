from __future__ import annotations


def pilot_run_events_channel(run_id: str) -> str:
    return f"research:agent:pilot:{run_id}:events"
