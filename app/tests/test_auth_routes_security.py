from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.routing import APIRoute

import app.interfaces.endpoints.agent_routes as agent_routes
import app.interfaces.endpoints.auth_routes as auth_routes
import app.interfaces.middleware.auth as auth_middleware
from app.domain.agent.security import SecurityContext
from app.domain.security import BrowserSession, Principal
from app.main import app


class FakeAuthService:
    def __init__(self, sessions: dict[str, BrowserSession]) -> None:
        self.sessions = sessions
        self.deleted: list[str | None] = []

    async def get_browser_session(self, session_id: str | None):
        return self.sessions.get(session_id or "")

    def validate_csrf(self, *, session, method, origin, csrf_token):
        if method not in {"GET", "HEAD", "OPTIONS"}:
            if origin != "http://localhost:5173" or csrf_token != session.csrf_token:
                from app.application.errors.exceptions import ForbiddenError

                raise ForbiddenError("CSRF validation failed")

    @staticmethod
    def require_scopes(principal: Principal, required: set[str] | frozenset[str]):
        if not principal.has_scopes(required):
            from app.application.errors.exceptions import ForbiddenError

            raise ForbiddenError("Required scope missing")

    async def delete_session(self, session_id: str | None):
        self.deleted.append(session_id)


def _session(*scopes: str) -> BrowserSession:
    now = datetime.now(UTC)
    return BrowserSession(
        principal=Principal(
            actor_type="user",
            subject="user-123",
            issuer="https://identity.example.test/.well-known/sunmoonai-research-admin",
            app="research",
            surface="admin",
            audience="research-admin-client",
            actor_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
            display_name="Test User",
            email="user@example.test",
            roles=("agent_user",),
            scopes=frozenset(scopes),
            authenticated_at=now,
            expires_at=now + timedelta(minutes=10),
            policy_version="research-admin-v1",
        ),
        csrf_token="csrf-token-with-at-least-thirty-two-characters",
    )


@pytest.mark.asyncio
async def test_agent_routes_fail_closed_before_database_access(monkeypatch) -> None:
    fake = FakeAuthService({"no-scope": _session(), "admin": _session("research:admin")})
    monkeypatch.setattr(auth_middleware, "_auth_service", fake)
    monkeypatch.setattr(auth_routes, "_auth_service", fake)
    app.dependency_overrides[agent_routes.require_agent_v4_traffic_enabled] = lambda: None
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            anonymous = await client.post("/api/agent/sessions")
            assert anonymous.status_code == 401

            client.cookies.set("sunmoonai_research_admin_sid", "no-scope")
            denied = await client.post("/api/agent/sessions")
            assert denied.status_code == 403

            client.cookies.set("sunmoonai_research_admin_sid", "admin")
            me = await client.get("/api/auth/me")
            assert me.status_code == 200
            rendered = str(me.json())
            assert "subject" not in rendered
            assert "access_token" not in rendered
    finally:
        app.dependency_overrides.clear()


def test_principal_is_the_only_source_for_agent_security_context() -> None:
    principal = _session("research:admin").principal
    context = agent_routes.security_context_for(principal)

    assert context == SecurityContext(
        actor_id=str(principal.actor_id),
        roles=["agent_user"],
        permissions=["research:admin"],
    )


def test_every_agent_route_has_admin_auth_dependency() -> None:
    for route in app.routes:
        if not isinstance(route, APIRoute) or not route.path.startswith("/api/agent/"):
            continue
        calls = {
            getattr(dependency.call, "__name__", "")
            for dependency in route.dependant.dependencies
        }
        assert "dependency" in calls, route.path
