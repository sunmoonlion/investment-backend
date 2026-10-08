"""Release descriptor and onboarding boundaries; no external services."""

import httpx
import pytest
from pydantic import ValidationError
from test_workbench_routes import A, principal

from app.bootstrap.api import create_app
from app.interfaces.endpoints.workbench_routes import (
    require_workbench_enabled,
)
from app.interfaces.http.middleware.auth import get_web_current_user
from core.config import AgentDownload, Settings

RELEASE = dict(
    url="https://download.example/agent/v1/windows-x64.zip",
    version="0.2.0",
    zip_sha256="a" * 64,
    manifest_sha256="b" * 64,
    codex_version="0.155.1",
    size_bytes=174242372,
)


@pytest.mark.parametrize(
    "patch",
    [
        dict(url="http://download.example/a.zip"),
        dict(url="https://user:secret@example/a"),
        dict(url="https://example/a?token=x"),
        dict(url="https://example/a#x"),
        dict(zip_sha256="bad"),
        dict(size_bytes=0),
        dict(extra="secret"),
        dict(url="https://example:bad/a"),
        dict(url="https://example/\\a"),
    ],
)
def test_bad_release_is_configuration_error(patch):
    with pytest.raises(ValidationError):
        AgentDownload(**(RELEASE | patch))


@pytest.mark.parametrize("release", [None, RELEASE])
async def test_download_contract_is_authenticated_config_only_no_store(release):
    settings = Settings(WORKBENCH_AGENT_DOWNLOAD=release)
    app = create_app(settings)
    app.dependency_overrides[require_workbench_enabled] = lambda: None
    app.dependency_overrides[get_web_current_user] = lambda: principal(A)
    # Route uses the same validated backend configuration, never frontend env vars.
    from unittest.mock import patch

    with patch(
        "app.interfaces.endpoints.workbench_routes.get_settings", return_value=settings
    ):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            r = await client.get("/api/workbench/agent/download")
            assert r.status_code == 200
            assert r.json() == {"contract_version": 2, "download": release}
            assert r.headers["cache-control"] == "no-store"
            app.dependency_overrides.pop(get_web_current_user)
            denied = await client.get("/api/workbench/agent/download")
            assert denied.status_code in (401, 403)
            assert denied.headers["cache-control"] == "no-store"


@pytest.mark.parametrize(
    "path", ["/sandboxes/provision", "/sandboxes/relay-identity/rotate"]
)
async def test_onboarding_mutations_require_web_session_origin_and_csrf(
    monkeypatch, path
):
    from test_auth_routes_security import FakeAuthService, session

    import app.interfaces.http.middleware.auth as auth

    app = create_app(Settings())
    app.dependency_overrides[require_workbench_enabled] = lambda: None
    fake = FakeAuthService({"onboarding-member": session("web", "profile:read")})
    monkeypatch.setattr(auth, "web_auth_service", fake)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        body = {"expected_revision": "a" * 64} if path.endswith("rotate") else {}
        url = "/api/workbench" + path
        assert (await client.post(url, json=body)).status_code == 401
        client.cookies.set("sunmoonai_investment_web_sid", "onboarding-member")
        for headers in [
            {},
            {
                "Origin": "https://wrong.example",
                "X-CSRF-Token": "csrf-token-with-at-least-thirty-two-characters",
            },
            {"Origin": "http://localhost:3000", "X-CSRF-Token": "wrong"},
        ]:
            response = await client.post(url, json=body, headers=headers)
            assert response.status_code == 403
            assert response.headers["cache-control"] == "no-store"
