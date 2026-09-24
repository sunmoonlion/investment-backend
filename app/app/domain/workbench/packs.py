"""专家包与步骤契约（methods.md「专家包」、task-contract.md「步骤交回物」）。

专家包是数据：workflow[] 的每一步说清输入引用、方法文本、输出 schema、验收条目、不合格的去向。顾问按它逐步发 turn；
它不是 skill（做成 skill 等于整包交出去），也不写判断规则（F-POS-02）。第一期内置两份：SMOKE（跑机制）、DATA_QUERY（问数五阶段，工具随 0006 来）。
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class AcceptanceRule(Strict):
    """确定性检查。kind 决定怎么判；judge 全是纯函数（application/workbench/acceptance.py）。"""

    kind: Literal[
        "json_object",
        "required_keys",
        "non_empty",
        "list_min",
        "no_positioning_advice",
        "max_chars",
    ]
    keys: tuple[str, ...] = ()
    path: str | None = None
    min_items: int = 1
    max_chars: int = 20000
    message: str = ""


class StepContract(Strict):
    step_id: str
    step_version: str = "1"
    title: str
    input_refs: tuple[str, ...] = ()  # 上游步骤的 artifact 名（取最新版本）
    method_text: str
    tools: tuple[
        str, ...
    ] = ()  # 这一步允许的 MCP 工具（服务端按 token 鉴权；此处用于 turn 输入说明）
    output_artifact: str  # 交回物名
    output_schema: dict[str, Any] = Field(
        default_factory=dict
    )  # 给模型看的结构说明（JSON 对象）
    acceptance: tuple[AcceptanceRule, ...] = ()
    on_reject: Literal["rework", "back", "human"] = "rework"
    back_to: str | None = None
    max_reworks: int = 1
    mode: Literal["single", "compete"] = (
        "single"  # compete 第一期不开（C-A10），位置留好
    )
    reserve: str = "0.05"  # 这一步预留的预算（币种随 Task）


class ExpertPack(Strict):
    pack_id: str
    version: str
    profile_id: str
    title: str
    workflow: tuple[StepContract, ...]
    auto_allow: dict[str, bool] = Field(
        default_factory=lambda: {
            "file_change_in_project": True,
            "command_escalation": False,
        }
    )
    answers: dict[str, str] = Field(default_factory=dict)  # 六问
    requires_tools: bool = False

    def step(self, index: int) -> StepContract | None:
        return self.workflow[index] if 0 <= index < len(self.workflow) else None

    def index_of(self, step_id: str) -> int:
        for i, s in enumerate(self.workflow):
            if s.step_id == step_id:
                return i
        raise KeyError(step_id)


JSON_ONLY = (
    "Reply with ONE JSON object only, no markdown fences, no commentary before or after it. "
    "Do not modify any files for this step unless the step says so."
)

SMOKE = ExpertPack(
    pack_id="SMOKE",
    version="1",
    profile_id="SMOKE",
    title="机制烟测：两步一返工",
    workflow=(
        StepContract(
            step_id="plan",
            title="拆步",
            method_text="Read the user's question. Produce a short plan of at most 3 concrete steps to answer it using only files in the current project directory. "
            + JSON_ONLY,
            output_artifact="plan",
            output_schema={"plan": ["string"], "assumptions": ["string"]},
            acceptance=(
                AcceptanceRule(kind="json_object", message="交回物不是 JSON 对象"),
                AcceptanceRule(kind="required_keys", keys=("plan",), message="缺 plan"),
                AcceptanceRule(
                    kind="list_min", path="plan", min_items=1, message="plan 为空"
                ),
            ),
            on_reject="rework",
            max_reworks=1,
        ),
        StepContract(
            step_id="answer",
            title="按计划作答",
            input_refs=("plan",),
            method_text="Follow the plan artifact given below. Answer the user's question. Every factual claim must carry a citation to a file path in the project or the literal string 'none'. Leave the conclusion field empty: the user fills conclusions. "
            + JSON_ONLY,
            output_artifact="answer",
            output_schema={
                "answer": "string",
                "citations": ["string"],
                "conclusion": "",
            },
            acceptance=(
                AcceptanceRule(kind="json_object", message="交回物不是 JSON 对象"),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("answer", "citations"),
                    message="缺 answer 或 citations",
                ),
                AcceptanceRule(kind="non_empty", path="answer", message="answer 为空"),
                AcceptanceRule(
                    kind="no_positioning_advice",
                    path="answer",
                    message="含投资建议措辞（F-POS-04）",
                ),
            ),
            on_reject="human",
            max_reworks=1,
        ),
    ),
    answers={
        "解决什么": "验证顾问逐步驾驶同一个 Codex 的机制",
        "不解决什么": "任何真实投研问题",
    },
)

DATA_QUERY = ExpertPack(
    pack_id="DATA_QUERY",
    version="1",
    profile_id="DATA_QUERY",
    title="问数（第 23–25 课五阶段）",
    requires_tools=True,
    workflow=(
        StepContract(
            step_id="rewrite",
            title="改写问题",
            method_text="Rewrite the user's data question into an unambiguous analytical question: entities, metrics, period, grain, filters. "
            + JSON_ONLY,
            output_artifact="rewritten",
            output_schema={
                "question": "string",
                "entities": ["string"],
                "metrics": ["string"],
                "period": "string",
            },
            acceptance=(
                AcceptanceRule(kind="json_object"),
                AcceptanceRule(kind="required_keys", keys=("question", "metrics")),
            ),
            max_reworks=1,
        ),
        StepContract(
            step_id="sql_generate",
            title="生成 SQL",
            input_refs=("rewritten",),
            method_text="Using the schema tool, write ONE SQL statement answering the rewritten question. Explain each column's 口径. "
            + JSON_ONLY,
            tools=("describe_schema", "metric_definitions"),
            output_artifact="sql",
            output_schema={
                "sql": "string",
                "columns": [{"name": "string", "definition": "string"}],
            },
            acceptance=(
                AcceptanceRule(kind="json_object"),
                AcceptanceRule(kind="required_keys", keys=("sql",)),
                AcceptanceRule(kind="non_empty", path="sql"),
            ),
            max_reworks=2,
        ),
        StepContract(
            step_id="sql_execute",
            title="执行 SQL",
            input_refs=("sql",),
            method_text="Run the SQL with the run_sql tool. Return rows verbatim (max 200) and the data version reported by the tool. "
            + JSON_ONLY,
            tools=("run_sql",),
            output_artifact="rows",
            output_schema={"rows": [{}], "row_count": 0, "data_version": "string"},
            acceptance=(
                AcceptanceRule(kind="json_object"),
                AcceptanceRule(kind="required_keys", keys=("rows", "data_version")),
            ),
            on_reject="back",
            back_to="sql_generate",
            max_reworks=1,
        ),
        StepContract(
            step_id="normalize",
            title="整理结果",
            input_refs=("rewritten", "rows"),
            method_text="Normalize rows into the answer table: units, currency, period labels; flag missing values. "
            + JSON_ONLY,
            output_artifact="table",
            output_schema={"table": [{}], "notes": ["string"]},
            acceptance=(
                AcceptanceRule(kind="json_object"),
                AcceptanceRule(kind="required_keys", keys=("table",)),
            ),
            max_reworks=1,
        ),
        StepContract(
            step_id="final",
            title="成稿",
            input_refs=("rewritten", "table"),
            method_text="Write the research note: the answer table, each number with its data version and 口径, limitations. Conclusion field stays empty for the user. "
            + JSON_ONLY,
            output_artifact="note",
            output_schema={
                "answer": "string",
                "table": [{}],
                "citations": ["string"],
                "limitations": ["string"],
                "conclusion": "",
            },
            acceptance=(
                AcceptanceRule(kind="json_object"),
                AcceptanceRule(kind="required_keys", keys=("answer", "citations")),
                AcceptanceRule(kind="no_positioning_advice", path="answer"),
            ),
            on_reject="human",
            max_reworks=1,
        ),
    ),
    answers={
        "解决什么": "对已入库数据集的口径明确的数值问题",
        "不解决什么": "预测、评级、买卖建议",
    },
)

BUILTIN_PACKS: dict[str, ExpertPack] = {p.profile_id: p for p in (SMOKE, DATA_QUERY)}


def find_pack(profile_id: str, version: str | None = None) -> ExpertPack | None:
    pack = BUILTIN_PACKS.get(profile_id)
    if pack is None or (version not in (None, "", pack.version)):
        return None
    return pack
