"""向 knowmart 取分身清单（SDD 0027 P4，服务身份 knowmart:summon）。

所有者由调用方从用户自己的登录会话里取来；knowmart 只给这个所有者的、运行方是 investment 的、
没停用的版本，别的一律 404。环境变量见 service_client.py（`KNOWMART_INTERNAL_URL`、
`KNOWMART_SUMMON_SERVICE_*`），由部署从后端 config 的 service_clients 生成。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote

from app.application.errors.exceptions import BadGatewayError, ServiceUnavailableError
from app.domain.workbench.errors import KnowbotUnavailable, NoSuchExpert
from app.infrastructure.external.service_client import ServiceClient, service_relation

NOT_FOUND = frozenset({"knowbot_not_found", "not_found"})


class KnowmartSummonClient:
    def __init__(self, environ: Mapping[str, str] | None = None, **client_options: Any):
        self._environ = environ
        self._options = client_options
        self._client: ServiceClient | None = None

    def _connect(self) -> ServiceClient:
        if self._client is None:
            relation = service_relation("knowmart", "summon", self._environ)
            if relation is None:
                raise KnowbotUnavailable("summoning from knowmart is not configured")
            self._client = ServiceClient(relation, **self._options)
        return self._client

    async def summon(
        self, *, owner_subject: str, knowbot_id: str, version: str
    ) -> dict[str, Any]:
        path = (
            f"/owners/{quote(owner_subject, safe='')}/knowbots/"
            f"{quote(knowbot_id, safe='')}@{quote(version, safe='')}"
        )
        try:
            _, body = await self._connect().request("GET", path)
        except BadGatewayError as exc:
            if exc.code in NOT_FOUND:
                raise NoSuchExpert("no such knowbot", knowbot=knowbot_id) from None
            raise KnowbotUnavailable("knowmart refused the summon") from None
        except ServiceUnavailableError:
            raise KnowbotUnavailable("knowmart is unavailable") from None
        if (
            not isinstance(body, dict)
            or body.get("knowbot_id") != knowbot_id
            or body.get("version") != version
            or not isinstance(body.get("manifest"), dict)
            or not isinstance(body.get("digest"), str)
        ):
            raise KnowbotUnavailable("knowmart answered outside the contract")
        return {
            "manifest": body["manifest"],
            "digest": body["digest"],
            "name": body.get("name"),
            "tagline": body.get("tagline"),
        }
