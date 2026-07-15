from functools import lru_cache
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # 基础配置
    env: str = "development"
    log_level: str = "INFO"

    # 数据库（读 DATABASE_URL，自动补 +asyncpg 驱动前缀）
    database_url: str = "postgresql+asyncpg://research:research@localhost:5432/research"
    # 可选：Alembic 迁移专用账号。运行时仍使用 DATABASE_URL。
    migration_database_url: str | None = None

    @field_validator("database_url", "migration_database_url", mode="before")
    @classmethod
    def ensure_asyncpg(cls, v: str) -> str:
        if isinstance(v, str) and (
            v.startswith("postgresql://") or v.startswith("postgresql+asyncpg://")
        ):
            url = v.replace("postgresql://", "postgresql+asyncpg://", 1)
            parts = urlsplit(url)
            query = [
                (key, value)
                for key, value in parse_qsl(parts.query, keep_blank_values=True)
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
        return v

    # Redis（dbctl ACL 场景可设 REDIS_USER；仅 default 密码时可留空）
    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_db: int = 0
    redis_user: str | None = None
    redis_password: str | None = None

    # Casdoor BFF
    casdoor_endpoint: str = ""
    casdoor_client_id: str = ""
    casdoor_client_secret: str = ""
    casdoor_redirect_uri: str = ""
    casdoor_organization: str = "built-in"
    casdoor_application: str = "sunmoonai-research-admin"
    casdoor_discovery_url: str | None = None
    # Optional fixed in-cluster transport for discovery/token/JWKS. Published
    # metadata and token issuer still belong to CASDOOR_ENDPOINT.
    casdoor_backchannel_endpoint: str | None = None
    casdoor_verify_ssl: bool = True

    auth_http_timeout_seconds: float = 10.0
    auth_transaction_ttl_seconds: int = 300
    auth_discovery_cache_seconds: int = 300
    auth_jwks_cache_seconds: int = 300
    auth_clock_skew_seconds: int = 30
    auth_allowed_algorithms: str = "RS256,ES256"
    auth_policy_version: str = "research-admin-v1"
    session_cookie_secure: bool | None = None

    # Frontend
    # Used for post-login redirects from backend callback.
    frontend_base_url: str = "http://localhost:5173"
    frontend_allowed_origins: str | None = None

    # Session
    session_ttl_seconds: int = 3600
    agent_session_lock_ttl_seconds: int = 300
    agent_v4_traffic_enabled: bool = False
    agent_redis_key_prefix: str = "research:agent"

    # Independent Research worker -> Knowledge retrieval relation.
    knowledge_retrieval_url: str | None = Field(
        default=None,
        validation_alias="KNOWLEDGE_RETRIEVAL_URL",
    )
    knowledge_retrieval_service_application: str = Field(
        default="sunmoonai-research-knowledge-retrieve",
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_APPLICATION",
    )
    knowledge_retrieval_service_discovery_url: str | None = Field(
        default=None,
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_DISCOVERY_URL",
    )
    knowledge_retrieval_service_backchannel_endpoint: str | None = Field(
        default=None,
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_BACKCHANNEL_ENDPOINT",
    )
    knowledge_retrieval_service_client_id: str | None = Field(
        default=None,
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_CLIENT_ID",
    )
    knowledge_retrieval_service_client_secret: str | None = Field(
        default=None,
        validation_alias="KNOWLEDGE_RETRIEVAL_SERVICE_CLIENT_SECRET",
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

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        raw_origins = self.frontend_allowed_origins or self.frontend_base_url
        if any(item.strip() == "*" for item in raw_origins.split(",")):
            raise ValueError("credential CORS cannot use wildcard origin")
        if self.env not in {"development", "test"}:
            if not self.casdoor_verify_ssl:
                raise ValueError("CASDOOR_VERIFY_SSL must be true in production")
            for field, value in (
                ("CASDOOR_ENDPOINT", self.casdoor_endpoint),
                ("CASDOOR_REDIRECT_URI", self.casdoor_redirect_uri),
                ("FRONTEND_BASE_URL", self.frontend_base_url),
            ):
                if value and urlsplit(value).scheme != "https":
                    raise ValueError(f"{field} must use HTTPS in production")
        return self

    @property
    def casdoor_discovery_endpoint(self) -> str:
        if self.casdoor_discovery_url:
            return self.casdoor_discovery_url
        if not self.casdoor_endpoint:
            return ""
        return f"{self.casdoor_endpoint.rstrip('/')}/.well-known/openid-configuration"

    @property
    def auth_allowed_algorithm_list(self) -> tuple[str, ...]:
        values = tuple(
            item.strip() for item in self.auth_allowed_algorithms.split(",") if item.strip()
        )
        if not values:
            raise ValueError("AUTH_ALLOWED_ALGORITHMS cannot be empty")
        allowed = {"RS256", "RS384", "RS512", "ES256", "ES384", "ES512"}
        if set(values) - allowed:
            raise ValueError(
                "AUTH_ALLOWED_ALGORITHMS must contain only configured asymmetric algorithms"
            )
        return values

    @property
    def frontend_origin_list(self) -> tuple[str, ...]:
        raw = self.frontend_allowed_origins or self.frontend_base_url
        values: list[str] = []
        for item in raw.split(","):
            parsed = urlsplit(item.strip())
            if not parsed.scheme or not parsed.hostname:
                continue
            port = f":{parsed.port}" if parsed.port is not None else ""
            values.append(f"{parsed.scheme}://{parsed.hostname}{port}")
        return tuple(dict.fromkeys(values))

    @property
    def auth_cookie_secure(self) -> bool:
        if self.session_cookie_secure is not None:
            return self.session_cookie_secure
        return self.env not in {"development", "test"}

    # Celery（应用层只读 CELERY_BROKER_URL；k8s 按 Deployment 注入 producer/worker 账号）
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

    @property
    def celery_enabled(self) -> bool:
        return bool(self.celery_broker_url)

    @property
    def knowledge_retrieval_enabled(self) -> bool:
        return bool(
            self.knowledge_retrieval_url
            and self.knowledge_retrieval_service_client_id
            and self.knowledge_retrieval_service_client_secret
        )

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache()
def get_settings() -> Settings:
    return Settings()
