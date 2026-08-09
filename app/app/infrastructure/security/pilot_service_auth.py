from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import Header

from app.application.errors.exceptions import (
    ForbiddenError,
    ServiceUnavailableError,
    UnauthorizedError,
)
from app.domain.security import Principal
from app.infrastructure.security import OidcProviderClient
from core.config import Settings, get_settings


class PilotServiceAuthVerifier:
    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        discovery_url = (
            self._settings.agent_pilot_internal_auth_discovery_url
            or self._settings.casdoor_discovery_url
        )
        service_endpoint = self._settings.casdoor_endpoint
        if discovery_url:
            parsed = urlsplit(discovery_url)
            if parsed.scheme in {"http", "https"} and parsed.hostname:
                service_endpoint = urlunsplit(
                    (parsed.scheme, parsed.netloc, "", "", "")
                )
        service_settings = self._settings.model_copy(
            update={
                "casdoor_endpoint": service_endpoint,
                "casdoor_application": (
                    self._settings.agent_pilot_internal_auth_application
                ),
                "casdoor_discovery_url": discovery_url,
                "casdoor_backchannel_endpoint": (
                    self._settings.agent_pilot_internal_auth_backchannel_endpoint
                ),
                "casdoor_client_id": (
                    self._settings.agent_pilot_internal_auth_audience or ""
                ),
                "casdoor_client_secret": "",
                "casdoor_redirect_uri": "",
            }
        )
        service_profile = replace(
            self._settings.browser_profile("admin"),
            client_id=self._settings.agent_pilot_internal_auth_audience or "",
            client_secret="",
            redirect_uri="",
            application=self._settings.agent_pilot_internal_auth_application,
            policy_version=(self._settings.agent_pilot_internal_auth_policy_version),
            required_scopes=(self._settings.agent_pilot_internal_auth_required_scope,),
        )
        self._oidc = OidcProviderClient(service_settings, service_profile)

    async def verify(self, encoded: str) -> Principal:
        self._settings.require_agent_pilot()
        expected_audience = self._settings.agent_pilot_internal_auth_audience
        allowed_subjects = self._settings.agent_pilot_internal_auth_subject_list
        if not expected_audience or not allowed_subjects:
            raise ServiceUnavailableError(
                "pilot service identity binding is not configured"
            )
        metadata = await self._oidc.get_metadata()
        claims = await self._oidc.verify_access_token(
            encoded,
            audience=expected_audience,
            metadata=metadata,
        )
        subject = claims.get("sub")
        if not isinstance(subject, str) or subject not in allowed_subjects:
            raise ForbiddenError("pilot service subject is not bound")
        self._validate_scope_shape(claims)
        return Principal(
            actor_type="service",
            subject=subject,
            issuer=metadata.issuer,
            app=self._settings.app_slug,
            surface="internal",
            audience=expected_audience,
            roles=(),
            scopes=frozenset({self._settings.agent_pilot_internal_auth_required_scope}),
            authenticated_at=datetime.fromtimestamp(int(claims["iat"]), tz=UTC),
            expires_at=datetime.fromtimestamp(int(claims["exp"]), tz=UTC),
            policy_version=(self._settings.agent_pilot_internal_auth_policy_version),
        )

    @staticmethod
    def _validate_scope_shape(claims: dict[str, Any]) -> None:
        raw_scope = claims.get("scope", claims.get("scp"))
        if raw_scope is None or isinstance(raw_scope, str):
            return
        if isinstance(raw_scope, list) and all(
            isinstance(item, str) for item in raw_scope
        ):
            return
        raise UnauthorizedError("pilot service token scope invalid")


_verifier: PilotServiceAuthVerifier | None = None


def get_pilot_service_auth_verifier() -> PilotServiceAuthVerifier:
    global _verifier
    if _verifier is None:
        _verifier = PilotServiceAuthVerifier()
    return _verifier


def _bearer_token(authorization: str | None) -> str:
    if not authorization:
        raise UnauthorizedError("pilot service bearer token required")
    try:
        scheme, encoded = authorization.split(" ", 1)
    except ValueError as exc:
        raise UnauthorizedError("pilot service bearer token required") from exc
    if scheme.lower() != "bearer" or not encoded.strip():
        raise UnauthorizedError("pilot service bearer token required")
    return encoded.strip()


async def require_pilot_service(
    authorization: str | None = Header(default=None, alias="Authorization"),
) -> Principal:
    return await get_pilot_service_auth_verifier().verify(_bearer_token(authorization))
