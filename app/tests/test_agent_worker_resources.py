"""Celery task boundaries must not leak pools into the next task's event loop."""

from __future__ import annotations

import subprocess
import sys

import pytest

from app.tasks import agent_delivery as tasks


@pytest.mark.parametrize("first", ["durable_delivery", "agent_delivery"])
def test_transport_modules_import_in_either_order(first):
    second = "agent_delivery" if first == "durable_delivery" else "durable_delivery"
    result = subprocess.run(
        [sys.executable, "-c", f"import app.tasks.{first}; import app.tasks.{second}"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("entry", ["pump", "execute"])
@pytest.mark.parametrize("fails", [False, True])
def test_every_agent_task_closes_loop_bound_resources(monkeypatch, entry, fails):
    cleaned = []

    class Resource:
        def __init__(self, name):
            self.name = name

        async def shutdown(self):
            cleaned.append(self.name)

    async def work(*args):
        if fails:
            raise RuntimeError("injected work failure")

    monkeypatch.setattr(tasks, "get_redis", lambda: Resource("redis"))
    monkeypatch.setattr(tasks, "get_postgres", lambda: Resource("postgres"))
    monkeypatch.setattr(tasks, "pump", work)
    monkeypatch.setattr(tasks, "execute_command", work)
    call = (
        tasks.pump_agent_delivery.run
        if entry == "pump"
        else lambda: tasks.run_agent_delivery.run("test")
    )
    if fails:
        with pytest.raises(RuntimeError, match="injected"):
            call()
    else:
        call()
    assert cleaned == ["redis", "postgres"]
