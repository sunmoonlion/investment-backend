"""顾问：表驱动的工作流解释器（methods.md「编排就是逐步发 turn」）。不调模型；对用户自己的 Codex thread 逐步发 turn。

一步的循环：开 Attempt（预留预算）→ 组 turn 输入（方法文本 + 上游 Artifact + 输出说明）→ turn/start → 等 turn/completed →
按预算账扣 token → 确定性验收 → 通过：落新版本 Artifact、游标 +1；不通过：按 on_reject 返工 / 回退 / 交人。
崩了重启就从游标与 Artifact 版本续：这里没有任何内存里的必需状态。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Protocol

from app.application.workbench.acceptance import judge
from app.application.workbench.ledger import Ledger
from app.domain.workbench.errors import BudgetExhausted
from app.domain.workbench.models import InteractionPrompt
from app.domain.workbench.packs import ExpertPack, StepContract, find_pack
from app.domain.workbench.states import AttemptState, TaskState, WaitingReason
from app.infrastructure.workbench.repository import WorkbenchRepository

log = logging.getLogger(__name__)


@dataclass
class TurnResult:
    turn_id: str | None
    final_text: str | None
    tokens: dict[str, Any]
    error: str | None = None
    environment_lost: bool = False


class TurnDriver(Protocol):
    """runner 的 SandboxLink 实现它：在给定 thread 上发一个 turn 并等它结束。"""

    async def run_turn(
        self,
        thread_id: str,
        text: str,
        *,
        timeout: float = 900,  # noqa: ASYNC109
    ) -> TurnResult: ...


class Pricing:
    """token → 费用。第一期一张粗表；厂商回报的费用字段出现后以它为准。"""

    def __init__(
        self,
        per_1k: dict[str, Decimal] | None = None,
        default_per_1k: Decimal = Decimal("0.01"),
    ):
        self.per_1k = per_1k or {}
        self.default = default_per_1k

    def cost(self, tokens: dict[str, Any], model: str | None = None) -> Decimal:
        total = (
            tokens.get("total", {}) if isinstance(tokens.get("total"), dict) else tokens
        )
        n = int(
            total.get("totalTokens")
            or (int(total.get("inputTokens", 0)) + int(total.get("outputTokens", 0)))
        )
        rate = self.per_1k.get(model or "", self.default)
        return (Decimal(n) / Decimal(1000) * rate).quantize(Decimal("0.000001"))


class Advisor:
    def __init__(
        self, repo_factory, driver: TurnDriver, *, pricing: Pricing | None = None
    ):
        self.repo_factory = repo_factory  # async ctx manager -> AsyncSession
        self.driver = driver
        self.pricing = pricing or Pricing()

    # ---------------- 入口 ----------------
    async def drive(self, task_id: str) -> str:
        """把一个 Task 推到停下为止：终态，或 WAITING（等人 / 等预算 / 等环境）。返回停下时的状态。"""
        async with self.repo_factory() as s:
            repo = WorkbenchRepository(s)
            led = Ledger(repo)
            task = await repo.get_task(task_id)
            if task["state"] == TaskState.RECEIVED:
                await led.transition(task_id, TaskState.VALIDATING)
                task = await repo.get_task(task_id)
            if task["state"] == TaskState.VALIDATING:
                pack = find_pack(task["profile_id"], task["profile_version"])
                if pack is None:
                    await led.transition(
                        task_id,
                        TaskState.REJECTED,
                        reason={
                            "code": "no_expert_pack",
                            "message": "这个问题不在专家范围，继续自己用 Codex 即可",
                        },
                    )
                    return TaskState.REJECTED
                if not task["thread_id"]:
                    await led.transition(
                        task_id,
                        TaskState.REJECTED,
                        reason={
                            "code": "no_thread",
                            "message": "会话还没有 Codex thread",
                        },
                    )
                    return TaskState.REJECTED
                await repo.cas_task(
                    task_id,
                    expected_version=(await repo.get_task(task_id))["state_version"],
                    workflow_version=pack.version,
                    expert_pack_version=pack.version,
                    acceptance_contract={
                        "pack_id": pack.pack_id,
                        "steps": [st.step_id for st in pack.workflow],
                    },
                )
                await repo.session.commit()
                await led.transition(task_id, TaskState.QUEUED)
        while True:
            state = await self._step_once(task_id)
            if state != TaskState.QUEUED:
                return state

    # ---------------- 一步 ----------------
    async def _step_once(self, task_id: str) -> str:
        async with self.repo_factory() as s:
            repo = WorkbenchRepository(s)
            led = Ledger(repo)
            task = await repo.get_task(task_id)
            if task["state"] not in (TaskState.QUEUED, TaskState.RUNNING):
                return task["state"]
            if task["cancel_requested_at"] is not None:
                return (
                    await led.transition(
                        task_id, TaskState.CANCELLED, reason={"by": "user"}
                    )
                )["state"]
            pack = find_pack(task["profile_id"], task["profile_version"])
            assert pack is not None
            step = pack.step(int(task["current_step"]))
            if step is None:
                return await self._finish(led, repo, task, pack)
            reworks = sum(
                1
                for a in await repo.list_attempts(task_id)
                if a["step_id"] == step.step_id
                and a["status"] == AttemptState.FAILED
                and a["failure_code"] == "acceptance"
            )
            inputs = {}
            for name in step.input_refs:
                art = await repo.get_artifact(task_id=task_id, name=name)
                if art is None:
                    return (
                        await led.transition(
                            task_id,
                            TaskState.FAILED,
                            reason={
                                "code": "missing_input",
                                "step": step.step_id,
                                "artifact": name,
                            },
                        )
                    )["state"]
                inputs[name] = {"version": art["version"], "content": art["content"]}
            try:
                opened = await led.open_attempt(
                    task_id,
                    step_id=step.step_id,
                    step_version=step.step_version,
                    input_artifact_versions=[
                        {"name": k, "version": v["version"]} for k, v in inputs.items()
                    ],
                    reserve=Decimal(step.reserve),
                    meta={"reworks": reworks},
                )
            except BudgetExhausted as exc:
                log.info("budget exhausted before step %s: %s", step.step_id, exc)
                return await self._hard_stop(led, repo, task_id)
            attempt_id = opened["attempt_id"]
            await repo.append_event(
                session_id=str(task["session_id"]),
                kind="task",
                event_type="step/started",
                payload={
                    "step_id": step.step_id,
                    "title": step.title,
                    "attempt_id": attempt_id,
                    "rework": reworks,
                },
                task_id=task_id,
                attempt_id=attempt_id,
            )
            await repo.session.commit()
            thread_id = task["thread_id"]
            question = task["original_input"].get("input", {})
            text = self.compose(step, question, inputs, reworks)

        result = await self.driver.run_turn(thread_id, text)

        async with self.repo_factory() as s:
            repo = WorkbenchRepository(s)
            led = Ledger(repo)
            await led.attempt_transition(
                attempt_id, AttemptState.RUNNING, turn_id=result.turn_id
            )
            task = await repo.get_task(task_id)
            if result.environment_lost or (result.error and not result.final_text):
                await led.attempt_transition(
                    attempt_id,
                    AttemptState.FAILED,
                    failure_code="environment"
                    if result.environment_lost
                    else "turn_error",
                    retryable=True,
                )
                reason = WaitingReason.ENVIRONMENT if result.environment_lost else None
                if reason:
                    return (
                        await led.transition(
                            task_id,
                            TaskState.WAITING,
                            waiting_reason=reason,
                            reason={"error": result.error},
                        )
                    )["state"]
                return (
                    await led.transition(
                        task_id, TaskState.FAILED, reason={"error": result.error}
                    )
                )["state"]
            consumed = self.pricing.cost(result.tokens, task.get("model"))
            budget = task["budget"]
            available = Decimal(str(budget["limit"])) - Decimal(str(budget["used"]))
            if consumed > available:
                await led.attempt_transition(
                    attempt_id,
                    AttemptState.BUDGET_EXCEEDED,
                    consumed=consumed,
                    tokens=result.tokens,
                )
                return TaskState.WAITING
            verdict = judge(result.final_text, step.acceptance)
            if verdict.ok:
                art = await repo.put_artifact(
                    task_id=task_id,
                    attempt_id=attempt_id,
                    name=step.output_artifact,
                    kind="step",
                    content=verdict.parsed,
                )
                await led.attempt_transition(
                    attempt_id,
                    AttemptState.COMPLETED,
                    consumed=consumed,
                    tokens=result.tokens,
                    output_artifacts=[
                        {
                            "name": art["name"],
                            "version": art["version"],
                            "id": art["id"],
                        }
                    ],
                )
                task = await repo.get_task(task_id)
                await repo.cas_task(
                    task_id,
                    expected_version=int(task["state_version"]),
                    current_step=int(task["current_step"]) + 1,
                )
                await repo.append_event(
                    session_id=str(task["session_id"]),
                    kind="task",
                    event_type="step/accepted",
                    payload={
                        "step_id": step.step_id,
                        "artifact": art,
                        "checks": verdict.checks,
                        "consumed": str(consumed),
                    },
                    task_id=task_id,
                    attempt_id=attempt_id,
                )
                await repo.session.commit()
                if pack.step(int(task["current_step"]) + 1) is None:
                    return await self._finish(
                        led, repo, await repo.get_task(task_id), pack
                    )
                await self._requeue(led, task_id)
                return TaskState.QUEUED
            # 不合格
            await led.attempt_transition(
                attempt_id,
                AttemptState.FAILED,
                consumed=consumed,
                tokens=result.tokens,
                failure_code="acceptance",
                retryable=True,
            )
            await repo.append_event(
                session_id=str(task["session_id"]),
                kind="task",
                event_type="step/rejected",
                payload={
                    "step_id": step.step_id,
                    "failures": verdict.failures,
                    "checks": verdict.checks,
                    "rework": reworks,
                    "on_reject": step.on_reject,
                },
                task_id=task_id,
                attempt_id=attempt_id,
            )
            await repo.session.commit()
            if reworks < step.max_reworks:  # 还有返工额度：重做本步
                await self._requeue(led, task_id)
                return TaskState.QUEUED
            if step.on_reject == "back" and step.back_to:  # 回到指定的前一步
                task = await repo.get_task(task_id)
                await repo.cas_task(
                    task_id,
                    expected_version=int(task["state_version"]),
                    current_step=pack.index_of(step.back_to),
                )
                await repo.session.commit()
                await self._requeue(led, task_id)
                return TaskState.QUEUED
            # 返工用尽：交人（AT-24）
            it = await led.open_interaction(
                task_id,
                kind="input",
                prompt=InteractionPrompt(
                    title=f"第「{step.title}」步没有通过验收",
                    question="要继续返工、跳过这一步交回现有产物，还是停止？",
                    options=[
                        {"id": "rework", "label": "再试一次"},
                        {"id": "stop", "label": "停止"},
                    ],
                    subject={
                        "step_id": step.step_id,
                        "failures": verdict.failures,
                        "last_output": (result.final_text or "")[:2000],
                    },
                    unknowns=verdict.failures,
                ),
                waiting_reason=WaitingReason.INPUT,
                attempt_id=attempt_id,
            )
            log.info(
                "step %s handed to human interaction=%s",
                step.step_id,
                it["interaction_id"],
            )
            return TaskState.WAITING

    async def _requeue(self, led: Ledger, task_id: str) -> bool:
        async with self.repo_factory() as s:
            repo = WorkbenchRepository(s)
            task = await repo.get_task(task_id)
            if task["state"] == TaskState.RUNNING:
                await Ledger(repo).transition(task_id, TaskState.QUEUED)
                return True
            return task["state"] == TaskState.QUEUED

    async def _hard_stop(
        self, led: Ledger, repo: WorkbenchRepository, task_id: str
    ) -> str:
        task = await repo.get_task(task_id)
        # 没有 Attempt 可开：直接进 WAITING(RESOURCE)，让用户追加
        await led.open_interaction(
            task_id,
            kind="resource",
            prompt=InteractionPrompt(
                title="预算不足以开始下一步",
                question="是否追加预算继续？",
                options=[
                    {"id": "topup", "label": "追加"},
                    {"id": "stop", "label": "停止"},
                ],
                subject={"budget": task["budget"]},
            ),
            waiting_reason=WaitingReason.RESOURCE,
        )
        return TaskState.WAITING

    async def _finish(
        self,
        led: Ledger,
        repo: WorkbenchRepository,
        task: dict[str, Any],
        pack: ExpertPack,
    ) -> str:
        task_id = str(task["id"])
        last = pack.workflow[-1]
        result_art = await repo.get_artifact(task_id=task_id, name=last.output_artifact)
        attempts = await repo.list_attempts(task_id)
        did = [f"{a['step_id']}: {a['status']}" for a in attempts]
        verified = [e for e in await repo.list_artifacts(task_id)]
        handback = {
            "did": did,
            "verified": [
                {"artifact": a["name"], "version": a["version"], "digest": a["digest"]}
                for a in verified
            ],
            "unknown": [],
            "workspace_restore": "顾问没有直接改动工作区文件；如有改动见各步 Artifact",
            "budget": task["budget"],
        }
        hb = await repo.put_artifact(
            task_id=task_id,
            attempt_id=None,
            name="handback",
            kind="handback",
            content=handback,
        )
        await repo.session.commit()
        task = await repo.get_task(task_id)
        if task["state"] == TaskState.QUEUED:  # 重启后从游标续到末尾的情形
            await led.transition(task_id, TaskState.RUNNING)
        r = await led.transition(
            task_id,
            TaskState.SUCCEEDED,
            terminal_result_ref=result_art["id"] if result_art else hb["id"],
        )
        return r["state"]

    # ---------------- turn 输入 ----------------
    @staticmethod
    def compose(
        step: StepContract,
        question: dict[str, Any],
        inputs: dict[str, Any],
        reworks: int,
    ) -> str:
        parts = [
            f"# Advisor step: {step.title} ({step.step_id}, v{step.step_version})",
            "You are executing one step of a supervised research workflow. Do exactly this step, nothing beyond it.",
            "",
            "## The user's question",
            json.dumps(question, ensure_ascii=False),
            "",
            "## Method for this step",
            step.method_text,
        ]
        if step.tools:
            parts += ["", "## Tools allowed in this step", ", ".join(step.tools)]
        if inputs:
            parts += ["", "## Inputs (fixed artifact versions from earlier steps)"]
            for name, v in inputs.items():
                parts.append(f"### {name} (v{v['version']})")
                parts.append(json.dumps(v["content"], ensure_ascii=False)[:8000])
        parts += [
            "",
            "## Output",
            f"Produce the artifact `{step.output_artifact}` as ONE JSON object with this shape:",
            json.dumps(step.output_schema, ensure_ascii=False),
        ]
        if reworks:
            parts += [
                "",
                f"## Note: this is rework #{reworks}; the previous attempt failed acceptance. Follow the output shape exactly.",
            ]
        return "\n".join(parts)
