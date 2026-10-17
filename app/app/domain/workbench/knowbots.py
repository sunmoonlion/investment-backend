"""用户自己的分身（SDD 0027 P4）：从 knowmart 请来的清单，变成工作台能跑的专家包。

清单外层（`knowbot-manifest/v1`）归 knowmart，里面的 `pack` 就是工作台的专家包形状，由这里
按 ExpertPack 完整校验。分身只归请它的那个用户：专家 id 写作 `knowbot:<分身>@<版本>`，钉在
委托上的是这一版的包快照与摘要，之后编排、底稿、验收都读快照，knowmart 停了也不影响在跑的。

分身是用户的方法，不是我们的：工具与审批只能比平台默认更严，不许自带命令放行前缀；
不许用数据线的工具（`C-L2`：分身运行时只读用户自己的数据）。读用户数据只有两个工具，
`list_my_data` 与 `read_my_data`，经工作台转到 knowlab，只读这一版清单 `data_bindings` 里的（0027 §3.5）。
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic import ValidationError

from app.domain.workbench.errors import KnowbotInvalid
from app.domain.workbench.packs import ExpertPack
from app.domain.workbench.tool_policy import TIER_ORDER, ToolPolicy

PREFIX = "knowbot:"
MANIFEST_SCHEMA = "knowbot-manifest/v1"
RUNTIME = "investment"
KNOWBOT_ID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
VERSION = r"[1-9][0-9]{0,5}"
REF = re.compile(rf"^({KNOWBOT_ID})@({VERSION})$")
PROFILE = re.compile(rf"^{PREFIX}({KNOWBOT_ID})@({VERSION})$")
# 分身能用的工具：只有读用户自己数据的这两个（0027 §3.5）
DATA_TOOLS: frozenset[str] = frozenset({"list_my_data", "read_my_data"})
KNOWBOT_TOOLS: frozenset[str] = DATA_TOOLS
_PLATFORM = ToolPolicy()


def parse_ref(value: str | None) -> tuple[str, str] | None:
    """`<分身>@<版本>`：knowmart 回跳带回来的。不合规则当作没带。"""
    found = REF.fullmatch(value or "")
    return (found.group(1), found.group(2)) if found else None


def profile_id(knowbot_id: str, version: str) -> str:
    return f"{PREFIX}{knowbot_id}@{version}"


def parse_profile(value: str | None) -> tuple[str, str] | None:
    found = PROFILE.fullmatch(value or "")
    return (found.group(1), found.group(2)) if found else None


def is_knowbot(value: str | None) -> bool:
    return (value or "").startswith(PREFIX)


def manifest_digest(manifest: dict[str, Any]) -> str:
    """与 knowmart 同一算法：外层规范 JSON（键排序、紧凑分隔、UTF-8）的 sha256。"""
    body = json.dumps(
        manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _no_looser_than_platform(policy: ToolPolicy) -> list[str]:
    problems = []
    for name in ("file_change_in_project", "file_change_outside", "command", "network"):
        if TIER_ORDER[getattr(policy, name)] < TIER_ORDER[getattr(_PLATFORM, name)]:
            problems.append(f"tool_policy.{name} is looser than the platform")
    if _PLATFORM.questions == "deny" and policy.questions == "allow":
        problems.append("tool_policy.questions is looser than the platform")
    if policy.max_questions > _PLATFORM.max_questions:
        problems.append("tool_policy.max_questions is above the platform")
    if policy.command_allow:
        problems.append("a knowbot cannot allow commands by prefix")
    return problems


def pack_from_manifest(
    manifest: object, *, knowbot_id: str, version: str, profile: str | None = None
) -> ExpertPack:
    """核清单并取出专家包；不合格抛 KnowbotInvalid（只说哪里不对，不回显内容）。

    profile：测试中的草稿还没有分身 id，用测试自己的专家 id（test_runs.trial_profile_id）。
    """
    if not isinstance(manifest, dict):
        raise KnowbotInvalid("the knowbot manifest is not an object")
    problems: list[str] = []
    if manifest.get("schema") != MANIFEST_SCHEMA:
        problems.append(f"schema must be {MANIFEST_SCHEMA}")
    if manifest.get("runtime") != RUNTIME:
        problems.append(f"runtime must be {RUNTIME}")
    if manifest.get("version") != version:
        problems.append("version differs from the one summoned")
    raw = manifest.get("pack")
    if not isinstance(raw, dict):
        problems.append("pack must be an object")
    if problems:
        raise KnowbotInvalid("; ".join(problems), knowbot=knowbot_id)
    assert isinstance(raw, dict)
    body = {
        **raw,
        # 身份与给用户看的名字以外层为准：分身的 id 不由清单自己说
        "profile_id": profile or profile_id(knowbot_id, version),
        "name": str(manifest.get("name") or raw.get("name") or ""),
        "tagline": str(manifest.get("tagline") or raw.get("tagline") or ""),
        "internal": False,
        "status": "ready",
        "preparing_note": "",
    }
    try:
        pack = ExpertPack.model_validate(body)
    except ValidationError as exc:
        where = sorted({".".join(str(p) for p in e["loc"]) for e in exc.errors()})
        raise KnowbotInvalid(
            "the knowbot pack is not a valid expert pack: " + ", ".join(where[:8]),
            knowbot=knowbot_id,
        ) from None
    if pack.version != version:
        problems.append("pack.version differs from the version")
    problems += _no_looser_than_platform(pack.tool_policy)
    for step in pack.workflow:
        if step.tool_policy is not None and step.tool_policy.command_allow:
            problems.append(f"step {step.step_id} allows commands by prefix")
        extra = set(step.tools) - KNOWBOT_TOOLS
        if extra:
            problems.append(f"step {step.step_id} uses tools a knowbot cannot use")
    if pack.requires_tools:
        problems.append("a knowbot cannot use the platform's data")
    if problems:
        raise KnowbotInvalid("; ".join(problems), knowbot=knowbot_id)
    return pack


def data_bindings(manifest: object) -> dict[str, tuple[str, str]]:
    """清单里这一版能读的用户数据：数据 id → （版本，sha256）。"""
    raw = manifest.get("data_bindings") if isinstance(manifest, dict) else None
    found: dict[str, tuple[str, str]] = {}
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, dict) and isinstance(item.get("dataset_id"), str):
            found[item["dataset_id"]] = (str(item.get("version")), str(item.get("sha256")))
    return found
