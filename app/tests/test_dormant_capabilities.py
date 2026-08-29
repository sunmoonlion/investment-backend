"""休眠能力的声明与校验（REQ-009 的机制部分）。

**问题**：本仓有一批"代码在、没接线"的能力。它们最容易被误读成已可用——
看到 `Outbox` 仓库类就以为事件在投递，看到 `beat_schedule` 入口就以为有定时任务。
投影文档里列了清单，但**文档不会自己变假**：能力接上了、或判据锚点被改名了，
清单照旧写着，读的人照旧被误导。

**机制**：清单以可执行判据的形式声明在这里，每条两个方向都能失败——

  ``anchor_exists``   锚点还在吗。**为 False 说明判据自己失效了**（文件改名、
                      类改名），此时 ``still_dormant`` 会因为"找不到"而假性通过。
                      这是本项目反复踩过的坑：把"没找到"当成"不存在"。
  ``still_dormant``   还休眠着吗。为 False 说明**能力已接线**，声明过期——
                      要删掉本条，并同步更新 `repos/investment-app.md`「已知未实现」。

**机制的边界**：它保证**已声明的条目不会变陈旧**，但**发现不了新出现的休眠能力**
——那需要判断"这段代码本该接线却没接"，不可机械判定。新增休眠能力时手工加一条。
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _exists(rel: str) -> bool:
    return (ROOT / rel).exists()


@dataclass(frozen=True)
class Dormant:
    name: str
    kind: str  # deliberate=模板有意留白 / pending=欠账
    evidence: str
    anchor_exists: Callable[[], bool]
    still_dormant: Callable[[], bool]


def _grep_files(pattern: str, *, where: str = "app") -> list[str]:
    """返回 where 下命中 pattern 的文件（仓库相对路径，已排序）。

    用「命中文件集合等于某个确定清单」而不是「计数等于 N」——
    计数会在文件改名后仍然凑巧相等，集合不会。
    """
    rx = re.compile(pattern)
    base = ROOT / where
    if not base.exists():
        return []
    return sorted(
        str(p.relative_to(ROOT))
        for p in base.rglob("*.py")
        if "__pycache__" not in p.parts and rx.search(p.read_text(encoding="utf-8"))
    )


def _mounted_paths(prefix: str) -> list[str]:
    """create_app() 实际挂出来的、以 prefix 开头的路由。

    不要用文本匹配代替：router 前缀往往写在被引入的模块里，
    对 routes.py grep 会漏掉，判据就成了永远通过的空转。
    """
    from fastapi.routing import APIRoute

    from app.bootstrap.api import create_app

    return [
        r.path
        for r in create_app().routes
        if isinstance(r, APIRoute) and r.path.startswith(prefix)
    ]


def _domain_dirs() -> list[Path]:
    return [ROOT / "app/domain" / d for d in ("models", "repositories", "services")]


# 只认**共享（模板）**的 Outbox 符号。实例仓可能有自己的领域 outbox
# （如 info 的 DeliveryOutboxMessage，它是在用的），不能一见 "outbox" 就算命中。
# 词边界让 \bOutboxMessage\b 不会匹配到 DeliveryOutboxMessage。
_SHARED_OUTBOX = re.compile(
    r"\b(OutboxPublisher|OutboxRepository|SqlOutboxRepository"
    r"|OutboxMessage|InboxMessage)\b"
)


def _shared_outbox_used_by_services() -> bool:
    return any(
        _SHARED_OUTBOX.search(p.read_text(encoding="utf-8"))
        for p in (ROOT / "app/application/services").rglob("*.py")
    )


DORMANT: tuple[Dormant, ...] = (
    Dormant(
        name="domain/{models,repositories,services} 仍是模板空壳",
        kind="pending",
        evidence=(
            "本仓继承自模板的这三个包只有空 __init__.py；实际领域代码在 "
            "infrastructure/models/ 与 application/services/。"
            "即分层目录名与代码实际归属不一致，读目录名会误判"
        ),
        anchor_exists=lambda: all(d.is_dir() for d in _domain_dirs()),
        still_dormant=lambda: all(
            [p.name for p in d.iterdir() if p.suffix == ".py"] == ["__init__.py"]
            for d in _domain_dirs()
        ),
    ),
    Dormant(
        name="Outbox/Inbox 消费链路",
        kind="deliberate",
        evidence="Port、DTO、ORM、SQL 仓库类齐备，application/services 零调用",
        anchor_exists=lambda: (
            _exists("app/infrastructure/repositories/outbox.py")
            and _exists("app/application/ports/outbox.py")
        ),
        still_dormant=lambda: not _shared_outbox_used_by_services(),
    ),
    Dormant(
        name="web-interaction 运行时",
        kind="deliberate",
        evidence="默认适配器是 UnavailableWebInteractionAdapter，生产必定 503",
        anchor_exists=lambda: (
            "class UnavailableWebInteractionAdapter"
            in _read("app/application/services/web_interaction.py")
        ),
        still_dormant=lambda: (
            "return UnavailableWebInteractionAdapter()"
            in _read("app/application/services/web_interaction.py")
        ),
    ),
    Dormant(
        name="Celery 周期任务",
        kind="deliberate",
        evidence="Scheduler 是四个运行角色之一，但全仓无 beat_schedule 定义",
        anchor_exists=lambda: _exists("app/bootstrap/scheduler.py"),
        still_dormant=lambda: (
            not any(
                "beat_schedule" in p.read_text(encoding="utf-8")
                for p in (ROOT / "app").rglob("*.py")
            )
        ),
    ),
    Dormant(
        name="RunBudget 在生产生效",
        kind="pending",
        evidence=(
            "全仓仅三处引用：定义处、非生产图 first_m1_graph、其测试。"
            "生产链路 pilot_service 只有一行 budget_exceeded→failed 的状态映射，"
            "从不构造也不消费预算，故 budget_exceeded 在生产中不可达。"
            "载体是内存态 pydantic model，跨进程即失——归入 dev-plan 的 U3 四本账"
        ),
        anchor_exists=lambda: "class RunBudget" in _read("app/domain/agent/runtime.py"),
        still_dormant=lambda: (
            sorted(_grep_files("RunBudget"))
            == [
                "app/domain/agent/runtime.py",
                "app/infrastructure/graph/first_m1_graph.py",
            ]
        ),
    ),
    Dormant(
        name="Web 面接 Agent/Pilot",
        kind="pending",
        evidence="/api/web/v1 默认 Unavailable 适配器；Pilot 只经 internal 面暴露",
        anchor_exists=lambda: _exists("app/application/services/web_interaction.py"),
        still_dormant=lambda: (
            "return UnavailableWebInteractionAdapter()"
            in _read("app/application/services/web_interaction.py")
        ),
    ),
    Dormant(
        name="Attempt / Invocation 落库",
        kind="pending",
        evidence=(
            "只有 execution_identity_spike 里的内存类，infrastructure/models 与"
            "迁移链中都没有对应表"
        ),
        anchor_exists=lambda: _exists(
            "app/infrastructure/graph/execution_identity_spike.py"
        ),
        still_dormant=lambda: (
            not _grep_files(
                r"class (Attempt|Invocation)\b", where="app/infrastructure/models"
            )
        ),
    ),
    Dormant(
        name="AgentMemoryService",
        kind="pending",
        evidence="类在 application/agent/memory_service.py，生产无任何调用方",
        anchor_exists=lambda: _exists("app/application/agent/memory_service.py"),
        still_dormant=lambda: (
            _grep_files("AgentMemoryService")
            == ["app/application/agent/memory_service.py"]
        ),
    ),
    Dormant(
        name="CancelRunCommand 的 HTTP 端点",
        kind="pending",
        evidence="领域命令已定义，interfaces/ 下无对应端点",
        anchor_exists=lambda: (
            "CancelRunCommand" in _read("app/domain/agent/commands.py")
        ),
        still_dormant=lambda: (
            not _grep_files("CancelRunCommand", where="app/interfaces")
        ),
    ),
    Dormant(
        name="first_m1_graph 与两个 spike 不在生产链",
        kind="deliberate",
        evidence=(
            "first_m1_graph 只被 tests 与 scripts/agent_golden.py 用；"
            "execution_identity_spike / runtime_selection_spike 同理"
        ),
        anchor_exists=lambda: all(
            _exists(f"app/infrastructure/graph/{n}.py")
            for n in (
                "first_m1_graph",
                "execution_identity_spike",
                "runtime_selection_spike",
            )
        ),
        still_dormant=lambda: (
            not _grep_files(
                "first_m1_graph|execution_identity_spike", where="app/tasks"
            )
        ),
    ),
    Dormant(
        name="AgentProfile 在执行期生效",
        kind="pending",
        evidence=(
            "RunService.create_run 解析 profile 并把 key/version 写进 run 行，"
            "但 dispatch_agent_graph 只传 run_id / user_input / security_context——"
            "effective_config 到不了图里。两条生产图对 allowed_tools、denied_tools、"
            "model_key、system_prompt_id、memory_policy 的引用数均为 0。"
            "即：Profile 被记录，不被执行；它现在是审计字段，不是约束"
        ),
        anchor_exists=lambda: (
            "class AgentProfile" in _read("app/domain/agent/profiles.py")
            and _exists("app/application/agent/run_service.py")
        ),
        still_dormant=lambda: (
            not _grep_files(
                r"allowed_tools|denied_tools|model_key|system_prompt_id|memory_policy",
                where="app/tasks",
            )
        ),
    ),
)


@pytest.mark.parametrize("item", DORMANT, ids=lambda i: i.name)
def test_dormant_capability_anchor_still_exists(item: Dormant) -> None:
    """锚点消失 → 判据空转。必须先修判据，否则下一条检查是假通过。"""
    assert item.anchor_exists(), (
        f"休眠声明「{item.name}」的锚点不见了——判据已失效，会假性通过。"
        f"先把判据改到新位置；确已删除该能力，则删掉本条声明。"
    )


@pytest.mark.parametrize("item", DORMANT, ids=lambda i: i.name)
def test_declared_dormant_capability_is_still_dormant(item: Dormant) -> None:
    """能力接线了 → 声明过期。删条目，并同步投影文档。"""
    assert item.still_dormant(), (
        f"休眠声明「{item.name}」已不成立——该能力看起来已接线。"
        f"请删掉本条声明，并同步更新 k8s:sunmoonai/docs/project-guide/"
        f"repos/investment-app.md 的「已知未实现」一节。依据：{item.evidence}"
    )


def test_every_dormant_entry_declares_why() -> None:
    """kind 只有两种：deliberate（有意留白）与 pending（欠账）。

    这一栏决定读者要不要担心：deliberate 是设计，pending 是债。
    """
    for item in DORMANT:
        assert item.kind in {"deliberate", "pending"}, item.name
        assert item.evidence.strip(), item.name
