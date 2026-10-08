"""Re-record only the new endpoint without regenerating unrelated scenario IDs.

PREVIEW_AGENT_DOWNLOAD_OUT=<web>/app/preview/fixtures pytest tests/test_preview_agent_download.py
"""

import json
import os
from pathlib import Path

import httpx
import pytest
from preview_agent_download import preview_download
from test_workbench_routes import A, principal

from app.bootstrap.api import create_app
from app.interfaces.endpoints import workbench_routes as routes
from app.interfaces.http.middleware.auth import get_web_current_user
from core.config import Settings


@pytest.mark.parametrize("scenario", ["empty", "full", "offline"])
async def test_record_download(monkeypatch, scenario):
    settings = Settings(WORKBENCH_AGENT_DOWNLOAD=preview_download(scenario))
    monkeypatch.setattr(routes, "get_settings", lambda: settings)
    app = create_app(settings)
    app.dependency_overrides[routes.require_workbench_enabled] = lambda: None
    app.dependency_overrides[get_web_current_user] = lambda: principal(A)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        response = await client.get("/api/workbench/agent/download")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert (response.json()["download"] is None) == (scenario == "empty")
    root = os.environ.get("PREVIEW_AGENT_DOWNLOAD_OUT")
    if root:
        directory = Path(root) / scenario
        path = directory / "manifest.json"
        manifest = json.loads(path.read_text())
        manifest["responses"] = [
            r
            for r in manifest["responses"]
            if r["path"] != "/api/workbench/agent/download"
        ]
        manifest["responses"].append(
            dict(
                method="GET",
                path="/api/workbench/agent/download",
                query="",
                status=response.status_code,
                content_type="application/json",
                file="agent-download.json",
            )
        )
        (directory / "agent-download.json").write_text(
            json.dumps(response.json(), ensure_ascii=False, indent=2) + "\n"
        )
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1) + "\n")
