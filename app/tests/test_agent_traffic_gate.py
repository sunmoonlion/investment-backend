from fastapi import HTTPException
from pytest import MonkeyPatch

from app.interfaces.endpoints.agent_routes import require_agent_v4_traffic_enabled
from core.config import get_settings


def test_agent_v4_traffic_gate_defaults_closed(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv("AGENT_V4_TRAFFIC_ENABLED", raising=False)
    get_settings.cache_clear()

    try:
        require_agent_v4_traffic_enabled()
    except HTTPException as exc:
        assert exc.status_code == 404
        assert exc.detail == "Agent v4 traffic is disabled"
    else:
        raise AssertionError("agent v4 traffic gate should default closed")
    finally:
        get_settings.cache_clear()


def test_agent_v4_traffic_gate_can_be_opened(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_V4_TRAFFIC_ENABLED", "true")
    get_settings.cache_clear()

    try:
        require_agent_v4_traffic_enabled()
    finally:
        get_settings.cache_clear()
