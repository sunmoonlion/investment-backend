from __future__ import annotations

from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration shared by the API and optional Celery worker.

    Browser identity settings are validated explicitly by
    :meth:`require_browser_identity` so a worker can start without receiving a
    browser client secret. The API calls that method before opening external
    connections.
    """

    env: str = "development"
    log_level: str = "INFO"
    service_name: str = "research-admin-backend"
    app_slug: str = "research"
    surface: str = "admin"

    database_url: str = "postgresql+asyncpg://research:research@localhost:5432/research"
    migration_database_url: str | None = None

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_user: str | None = None
    redis_password: str | None = None

    casdoor_endpoint: str = ""
    casdoor_discovery_url: str | None = None
    casdoor_backchannel_endpoint: str | None = None
    casdoor_client_id: str = ""
    casdoor_client_secret: str = ""
    casdoor_redirect_uri: str = ""
    casdoor_organization: str = "built-in"
    casdoor_application: str = "sunmoonai-research-admin"
    casdoor_verify_ssl: bool = True
    browser_identity_enabled: bool = True

    auth_http_timeout_seconds: float = Field(default=10.0, gt=0, le=60)
    auth_transaction_ttl_seconds: int = Field(default=300, ge=60, le=900)
    auth_discovery_cache_seconds: int = Field(default=300, ge=1, le=3600)
    auth_jwks_cache_seconds: int = Field(default=300, ge=1, le=3600)
    auth_clock_skew_seconds: int = Field(default=30, ge=0, le=120)
    auth_allowed_algorithms: str = "RS256,ES256"
    auth_policy_version: str = "research-admin-v1"
    auth_role_allowlist: str = ""
    auth_scope_allowlist: str = ""

    frontend_base_url: str = "http://localhost:5173"
    frontend_allowed_origins: str | None = None
    allowed_hosts: str = "localhost,127.0.0.1,testserver"

    session_ttl_seconds: int = Field(default=3600, ge=60, le=86400)
    session_cookie_secure: bool | None = None

    celery_broker_url: str | None = Field(
        default=None, validation_alias="CELERY_BROKER_URL"
    )
    celery_queue: str = Field(
        default="research.admin.default",
        validation_alias=AliasChoices("CELERY_QUEUE", "CELERY_TASK_DEFAULT_QUEUE"),
    )
    celery_result_backend: str | None = Field(
        default=None, validation_alias="CELERY_RESULT_BACKEND"
    )

    # Agent runtime (Research domain).
    agent_session_lock_ttl_seconds: int = Field(default=300, ge=1, le=3600)
    agent_v4_traffic_enabled: bool = Field(
        default=False, validation_alias="AGENT_V4_TRAFFIC_ENABLED"
    )
    agent_redis_key_prefix: str = Field(
        default="research:agent", validation_alias="AGENT_REDIS_KEY_PREFIX"
    )
    agent_pilot_enabled: bool = Field(
        default=False, validation_alias="AGENT_PILOT_ENABLED"
    )
    agent_pilot_internal_auth_application: str = Field(
        default="sunmoonai-research-runtime",
        validation_alias="AGENT_PILOT_INTERNAL_AUTH_APPLICATION",
    )
    agent_pilot_internal_auth_discovery_url: str | None = Field(
        default=None,
        validation_alias="AGENT_PILOT_INTERNAL_AUTH_DISCOVERY_URL",
    )
    agent_pilot_internal_auth_backchannel_endpoint: str | None = Field(
        default=None,
        validation_alias="AGENT_PILOT_INTERNAL_AUTH_BACKCHANNEL_ENDPOINT",
    )
    agent_pilot_internal_auth_audience: str | None = Field(
        default=None, validation_alias="AGENT_PILOT_INTERNAL_AUTH_AUDIENCE"
    )
    agent_pilot_internal_auth_subjects: str = Field(
        default="", validation_alias="AGENT_PILOT_INTERNAL_AUTH_SUBJECTS"
    )
    agent_pilot_internal_auth_required_scope: str = Field(
        default="research:runtime",
        validation_alias="AGENT_PILOT_INTERNAL_AUTH_REQUIRED_SCOPE",
    )
    agent_pilot_dataset_keys: str = Field(
        default="", validation_alias="AGENT_PILOT_DATASET_KEYS"
    )
    agent_pilot_llm_base_url: str = Field(
        default="", validation_alias="AGENT_PILOT_LLM_BASE_URL"
    )
    agent_pilot_llm_api_key: str | None = Field(
        default=None, validation_alias="AGENT_PILOT_LLM_API_KEY"
    )
    agent_pilot_llm_model: str = Field(
        default="qwen-plus", validation_alias="AGENT_PILOT_LLM_MODEL"
    )
    agent_pilot_llm_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=120,
        validation_alias="AGENT_PILOT_LLM_TIMEOUT_SECONDS",
    )

    # Knowledge retrieval client (cross-app relation names must NOT be rewritten).
    knowledge_retrieval_url: str | None = Field(
        default=None, validation_alias="KNOWLEDGE_RETRIEVAL_URL"
    )
    knowledge_retrieval_service_application: str = Field(
        default="sunmoonai-research-knowledge-retrieve",
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_APPLICATION",
    )
    knowledge_retrieval_service_discovery_url: str | None = Field(
        default=None, validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_DISCOVERY_URL"
    )
    knowledge_retrieval_service_backchannel_endpoint: str | None = Field(
        default=None,
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_BACKCHANNEL_ENDPOINT",
    )
    knowledge_retrieval_service_client_id: str | None = Field(
        default=None, validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_CLIENT_ID"
    )
    knowledge_retrieval_service_client_secret: str | None = Field(
        default=None, validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_CLIENT_SECRET"
    )
    knowledge_retrieval_service_scope: str = Field(
        default="knowledge:retrieve",
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_SCOPE",
    )
    knowledge_retrieval_timeout_seconds: float = Field(
        default=20.0,
        gt=0,
        le=120,
        validation_alias="KNOWLEDGE_RETRIEVAL_TIMEOUT_SECONDS",
    )

    @field_validator("database_url", "migration_database_url", mode="before")
    @classmethod
    def normalize_postgres_url(cls, value: str | None) -> str | None:
        if not isinstance(value, str) or not (
            value.startswith("postgresql://")
            or value.startswith("postgresql+asyncpg://")
        ):
            return value
        normalized = value.replace("postgresql://", "postgresql+asyncpg://", 1)
        parts = urlsplit(normalized)
        query = [
            (key, item)
            for key, item in parse_qsl(parts.query, keep_blank_values=True)
            if key != "sslmode"
        ]
        return urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                parts.path,
                urlencode(query),
                parts.fragment,
            )
        )

    @model_validator(mode="after")
    def validate_base_security(self) -> Settings:
        if self.surface != "admin":
            raise ValueError("tpl-admin-backend requires SURFACE=admin")
        if not self.app_slug or any(
            char not in "abcdefghijklmnopqrstuvwxyz0123456789-"
            for char in self.app_slug
        ):
            raise ValueError("APP_SLUG must use lowercase letters, digits and hyphens")
        origins = tuple(
            self._strict_origin(item, field="FRONTEND_ALLOWED_ORIGINS")
            for item in self._split_csv(self.frontend_origins_raw)
        )
        if not origins:
            raise ValueError(
                "FRONTEND_ALLOWED_ORIGINS must contain an absolute HTTP(S) origin"
            )
        if self.is_production and any(
            urlsplit(origin).scheme != "https" for origin in origins
        ):
            raise ValueError("FRONTEND_ALLOWED_ORIGINS must use HTTPS in production")
        if any(item.strip() == "*" for item in self._split_csv(self.allowed_hosts)):
            if self.is_production:
                raise ValueError("ALLOWED_HOSTS cannot use wildcard in production")
        if self.is_production and not self.casdoor_verify_ssl:
            raise ValueError("CASDOOR_VERIFY_SSL must be true in production")
        if self.is_production and self.session_cookie_secure is False:
            raise ValueError("SESSION_COOKIE_SECURE cannot be false in production")
        if self.agent_pilot_enabled:
            self.require_agent_pilot()
        if not self.browser_identity_enabled and not self.agent_pilot_enabled:
            raise ValueError(
                "BROWSER_IDENTITY_ENABLED can be false only for the isolated agent pilot"
            )
        return self

    @staticmethod
    def _split_csv(value: str) -> tuple[str, ...]:
        return tuple(item.strip() for item in value.split(",") if item.strip())

    @staticmethod
    def _strict_origin(value: str, *, field: str) -> str:
        if value == "*":
            raise ValueError(f"{field} cannot contain wildcard origin")
        try:
            parsed = urlsplit(value)
            port = f":{parsed.port}" if parsed.port is not None else ""
        except ValueError as exc:
            raise ValueError(f"{field} contains an invalid port") from exc
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(f"{field} must contain origin-only HTTP(S) URLs")
        return f"{parsed.scheme}://{parsed.hostname}{port}"

    @property
    def is_production(self) -> bool:
        return self.env not in {"development", "test"}

    @property
    def migration_url(self) -> str:
        return self.migration_database_url or self.database_url

    @property
    def casdoor_discovery_endpoint(self) -> str:
        if self.casdoor_discovery_url:
            return self.casdoor_discovery_url
        if not self.casdoor_endpoint:
            return ""
        return f"{self.casdoor_endpoint.rstrip('/')}/.well-known/openid-configuration"

    @property
    def auth_allowed_algorithm_list(self) -> tuple[str, ...]:
        values = self._split_csv(self.auth_allowed_algorithms)
        if not values:
            raise ValueError("AUTH_ALLOWED_ALGORITHMS cannot be empty")
        allowed = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
        if set(values) - allowed:
            raise ValueError(
                "AUTH_ALLOWED_ALGORITHMS must contain only asymmetric algorithms"
            )
        return values

    @property
    def frontend_origins_raw(self) -> str:
        return self.frontend_allowed_origins or self.frontend_base_url

    @property
    def frontend_origin_list(self) -> tuple[str, ...]:
        origins = [
            self._strict_origin(item, field="FRONTEND_ALLOWED_ORIGINS")
            for item in self._split_csv(self.frontend_origins_raw)
        ]
        return tuple(dict.fromkeys(origins))

    @property
    def auth_role_allowlist_items(self) -> frozenset[str]:
        return frozenset(self._split_csv(self.auth_role_allowlist))

    @property
    def auth_scope_allowlist_items(self) -> frozenset[str]:
        return frozenset(self._split_csv(self.auth_scope_allowlist))

    @property
    def allowed_host_list(self) -> tuple[str, ...]:
        return self._split_csv(self.allowed_hosts)

    @property
    def auth_cookie_secure(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.is_production

    @property
    def session_cookie_name(self) -> str:
        return f"sunmoonai_{self.app_slug}_{self.surface}_sid"

    @property
    def transaction_cookie_name(self) -> str:
        return f"sunmoonai_{self.app_slug}_{self.surface}_oidc_tx"

    @property
    def session_key_prefix(self) -> str:
        return f"{self.app_slug}:auth:{self.surface}:session:"

    @property
    def transaction_key_prefix(self) -> str:
        return f"{self.app_slug}:auth:{self.surface}:oidc:"

    @property
    def required_admin_scope(self) -> str:
        return f"{self.app_slug}:admin"

    @property
    def celery_enabled(self) -> bool:
        return bool(self.celery_broker_url)

    def require_browser_identity(self) -> None:
        required = {
            "CASDOOR_ENDPOINT": self.casdoor_endpoint,
            "CASDOOR_CLIENT_ID": self.casdoor_client_id,
            "CASDOOR_CLIENT_SECRET": self.casdoor_client_secret,
            "CASDOOR_REDIRECT_URI": self.casdoor_redirect_uri,
            "FRONTEND_BASE_URL": self.frontend_base_url,
        }
        missing = sorted(name for name, value in required.items() if not value.strip())
        if missing:
            raise ValueError(
                f"browser identity configuration missing: {', '.join(missing)}"
            )
        provider_origin = self._strict_origin(
            self.casdoor_endpoint, field="CASDOOR_ENDPOINT"
        )
        discovery = urlsplit(self.casdoor_discovery_endpoint)
        if (
            discovery.scheme not in {"http", "https"}
            or not discovery.hostname
            or discovery.username
            or discovery.password
            or discovery.query
            or discovery.fragment
            or discovery.path != "/.well-known/openid-configuration"
        ):
            raise ValueError(
                "CASDOOR_DISCOVERY_URL must use the standard discovery path"
            )
        discovery_origin = f"{discovery.scheme}://{discovery.hostname}" + (
            f":{discovery.port}" if discovery.port is not None else ""
        )
        if discovery_origin != provider_origin:
            raise ValueError(
                "CASDOOR_DISCOVERY_URL must use the CASDOOR_ENDPOINT origin"
            )
        if self.is_production and urlsplit(provider_origin).scheme != "https":
            raise ValueError("CASDOOR_ENDPOINT must use HTTPS in production")

        redirect = urlsplit(self.casdoor_redirect_uri)
        if (
            redirect.scheme not in {"http", "https"}
            or not redirect.hostname
            or redirect.username
            or redirect.password
            or redirect.query
            or redirect.fragment
            or redirect.path != "/api/auth/callback"
        ):
            raise ValueError("CASDOOR_REDIRECT_URI must end at /api/auth/callback")
        if self.is_production and redirect.scheme != "https":
            raise ValueError("CASDOOR_REDIRECT_URI must use HTTPS in production")
        frontend_origin = self._strict_origin(
            self.frontend_base_url, field="FRONTEND_BASE_URL"
        )
        if self.is_production and urlsplit(frontend_origin).scheme != "https":
            raise ValueError("FRONTEND_BASE_URL must use HTTPS in production")
        if (
            f"{redirect.scheme}://{redirect.hostname}"
            + (f":{redirect.port}" if redirect.port is not None else "")
            != frontend_origin
        ):
            raise ValueError(
                "CASDOOR_REDIRECT_URI must use the FRONTEND_BASE_URL origin"
            )
        if not self.frontend_origin_list:
            raise ValueError("FRONTEND_ALLOWED_ORIGINS must contain an absolute origin")
        if frontend_origin not in self.frontend_origin_list:
            raise ValueError("FRONTEND_ALLOWED_ORIGINS must include FRONTEND_BASE_URL")
        if not self.allowed_host_list:
            raise ValueError("ALLOWED_HOSTS cannot be empty")
        # Force algorithm validation during startup rather than first callback.
        _ = self.auth_allowed_algorithm_list


    @property
    def knowledge_retrieval_enabled(self) -> bool:
        return bool(
            self.knowledge_retrieval_url
            and self.knowledge_retrieval_service_client_id
            and self.knowledge_retrieval_service_client_secret
        )

    @property
    def agent_pilot_internal_auth_subject_list(self) -> frozenset[str]:
        return frozenset(self._split_csv(self.agent_pilot_internal_auth_subjects))

    @property
    def agent_pilot_dataset_key_list(self) -> tuple[str, ...]:
        return self._split_csv(self.agent_pilot_dataset_keys)

    def require_agent_pilot(self) -> None:
        if not self.agent_pilot_enabled:
            raise ValueError("AGENT_PILOT_ENABLED must be true")
        required = {
            "AGENT_PILOT_INTERNAL_AUTH_AUDIENCE": (
                self.agent_pilot_internal_auth_audience
            ),
            "AGENT_PILOT_INTERNAL_AUTH_SUBJECTS": (
                self.agent_pilot_internal_auth_subjects
            ),
            "AGENT_PILOT_DATASET_KEYS": self.agent_pilot_dataset_keys,
            "AGENT_PILOT_LLM_BASE_URL": self.agent_pilot_llm_base_url,
            "AGENT_PILOT_LLM_API_KEY": self.agent_pilot_llm_api_key,
            "AGENT_PILOT_LLM_MODEL": self.agent_pilot_llm_model,
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ValueError(
                f"agent pilot configuration missing: {', '.join(missing)}"
            )
        if not self.knowledge_retrieval_enabled:
            raise ValueError("Knowledge retrieval must be configured for agent pilot")

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
