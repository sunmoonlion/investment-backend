"""从专家入口请专家（PRD/apps/investment.md 7.3、F-PROJ-05）。

新建一段工作对话并立刻交给专家：建对话、建委托、交出方向盘、排上驾驶的命令，在同一次提交里。
哪一步不成，整个都不留下：不会剩一段空的对话（AT-INV-08）。
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.application.ports.workbench import WorkbenchStore
from app.application.workbench.ledger import Ledger
from app.application.workbench.session_service import SessionService
from app.domain.workbench.errors import IdempotencyConflict, NoSuchExpert
from app.domain.workbench.models import HandoverRequest
from app.domain.workbench.packs import ExpertPack, find_pack
from app.domain.workbench.projects import default_title


def expert_on_offer(profile_id: str) -> ExpertPack:
    pack = find_pack(profile_id)
    if pack is None or pack.internal:
        raise NoSuchExpert("no such expert", profile_id=profile_id)
    return pack


class DelegationService:
    def __init__(self, repo: WorkbenchStore):
        self.repo = repo

    async def from_expert_entry(
        self,
        *,
        owner_actor_id: str,
        project_id: str,
        profile_id: str,
        question: str,
        budget_limit: Decimal,
        budget_currency: str = "CNY",
        idempotency_key: str,
        tenant: str = "default",
    ) -> dict[str, Any]:
        pack = expert_on_offer(profile_id)

        def request(session_id: str) -> HandoverRequest:
            return HandoverRequest(
                idempotency_key=idempotency_key,
                session_id=session_id,
                profile_id=pack.profile_id,
                original_input={"text": question},
                budget_limit=budget_limit,
                budget_currency=budget_currency,
                client_context={"entry": "expert"},
            )

        async with self.repo.transaction():
            prior = await self.repo.find_task_by_idempotency(
                tenant=tenant,
                owner_actor_id=owner_actor_id,
                profile_id=pack.profile_id,
                idempotency_key=idempotency_key,
            )
            if prior is not None:
                # 同一把钥匙重发：是同一件事就还给原来的那一份，不再建一段对话
                same = request(str(prior["session_id"])).digest()
                if prior["request_digest"] != same or (
                    str(prior.get("project_id")) != project_id
                ):
                    raise IdempotencyConflict(
                        "same idempotency key with a different request",
                        task_id=str(prior["id"]),
                    )
                return {
                    "task_id": str(prior["id"]),
                    "session_id": str(prior["session_id"]),
                    "project_id": project_id,
                    "state": prior["state"],
                    "created": False,
                }
            started = await SessionService(self.repo).start(
                owner_actor_id=owner_actor_id,
                kind="work",
                project_id=project_id,
                title=default_title(question),
            )
            session_id = started["session_id"]
            made = await Ledger(self.repo).handover(
                request(session_id), owner_actor_id=owner_actor_id, tenant=tenant
            )
            session = await self.repo.get_session(session_id)
            # 这段对话还没有线：驾驶的命令会先把线起好（runner），所以只排这一条
            await self.repo.enqueue_command(
                session_id=session_id,
                sandbox_id=str(session["sandbox_id"]),
                kind="task.drive",
                payload={"task_id": made["task_id"]},
            )
            return {
                "task_id": made["task_id"],
                "session_id": session_id,
                "project_id": project_id,
                "state": made["state"],
                "created": True,
            }
