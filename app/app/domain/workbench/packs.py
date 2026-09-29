"""专家包与步骤契约（methods.md「专家包」、task-contract.md「步骤交回物」）。

专家包是数据：workflow[] 的每一步说清输入引用、方法文本、输出 schema、验收条目、不合格的去向。顾问按它逐步发 turn；
它不是 skill（做成 skill 等于整包交出去），也不写判断规则（F-POS-02）。内置三份：SMOKE（跑机制）、DATA_QUERY（问数五阶段，工具随 0006 来）、
FIN_REVIEW（财报体检七步，0008-info 段四）。
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
        "all_equal",  # path 处是列表，每一项的 field 都等于 equals
        "list_empty",  # path 处是列表，而且是空的
        "blank",  # path 处没有内容：缺失、null 或空白字符串
    ]
    keys: tuple[str, ...] = ()
    path: str | None = None
    min_items: int = 1
    max_chars: int = 20000
    field: str | None = None
    equals: str | int | float | bool | None = None
    message: str = ""  # 不通过时的说明
    label: str = ""  # 这条验收的名字，通过时也显示


class StepContract(Strict):
    step_id: str
    step_version: str = "1"
    title: str
    summary: str = ""  # 给用户看的一句白话。方法的原文不给用户看
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
    input_max_chars: int = 8000  # 每个上游交回物放进 turn 输入的字符上限；超出会注明


class DossierBlock(Strict):
    """底稿里的一块：取哪个交回物的哪一项，画成什么。"""

    key: str
    title: str
    kind: Literal["text", "list", "table", "tables", "checks", "coverage"]
    artifact: str  # 交回物名
    path: str | None = None  # 交回物里的哪一项；不给就是整个
    section: Literal["answer", "data", "verified", "notes", "limits", "sources"]
    folded: bool = False  # 默认折叠
    # 这一块还要带上的项：名字 → 路径
    also: dict[str, str] = Field(default_factory=dict)
    # 表的列：（交回物里的名字，给用户看的名字），按这个顺序排。不给就按行里有什么排什么
    columns: tuple[tuple[str, str], ...] = ()


class ExpertPack(Strict):
    pack_id: str
    version: str
    profile_id: str
    title: str
    name: str = ""  # 给用户看的名字。代号不给用户看
    tagline: str = ""
    internal: bool = False  # 只给我们自己测机制用，不进清单
    dossier: tuple[DossierBlock, ...] = ()
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
    name="机制烟测",
    tagline="验证专家逐步驾驶同一个 Codex 的机制",
    internal=True,
    workflow=(
        StepContract(
            step_id="plan",
            title="拆步",
            summary="把问题拆成不超过三步",
            method_text="Read the user's question. Produce a short plan of at most 3 concrete steps to answer it using only files in the current project directory. "
            + JSON_ONLY,
            output_artifact="plan",
            output_schema={"plan": ["string"], "assumptions": ["string"]},
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("plan",),
                    message="缺 plan",
                    label="有计划",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="plan",
                    min_items=1,
                    message="plan 为空",
                    label="计划不是空的",
                ),
            ),
            on_reject="rework",
            max_reworks=1,
        ),
        StepContract(
            step_id="answer",
            title="按计划作答",
            summary="按计划作答，每个说法带出处",
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
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("answer", "citations"),
                    message="缺 answer 或 citations",
                    label="有回答和出处",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="answer",
                    message="answer 为空",
                    label="回答不是空的",
                ),
                AcceptanceRule(
                    kind="no_positioning_advice",
                    path="answer",
                    message="含有评级、目标价或买卖建议的措辞",
                    label="没有评级、目标价、买卖建议",
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
    name="问数",
    tagline="对已有的数据问一个数",
    requires_tools=True,
    dossier=(
        DossierBlock(
            key="answer",
            title="回答",
            kind="text",
            artifact="note",
            path="answer",
            section="answer",
        ),
        # 表取整理那一步验过的交回物，不取成稿里模型重抄的那一份
        DossierBlock(
            key="result",
            title="结果表",
            kind="table",
            artifact="table",
            path="table",
            section="data",
            also={"notes": "notes"},
        ),
        DossierBlock(
            key="definitions",
            title="查询用的口径",
            kind="table",
            artifact="sql",
            path="columns",
            section="verified",
            also={"sql": "sql"},
            columns=(
                ("name", "列"),
                ("definition", "口径"),
            ),
        ),
        DossierBlock(
            key="rows",
            title="查询带回的原样结果",
            kind="table",
            artifact="rows",
            path="rows",
            section="verified",
            folded=True,
            also={"row_count": "row_count", "data_version": "data_version"},
        ),
        DossierBlock(
            key="limitations",
            title="局限",
            kind="list",
            artifact="note",
            path="limitations",
            section="limits",
        ),
        DossierBlock(
            key="citations",
            title="出处",
            kind="list",
            artifact="note",
            path="citations",
            section="sources",
        ),
    ),
    workflow=(
        StepContract(
            step_id="rewrite",
            title="改写问题",
            summary="把问题改写成没有歧义的：查什么、哪个期间、什么口径",
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
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("question", "metrics"),
                    message="缺改写后的问题或指标",
                    label="有改写后的问题和指标",
                ),
            ),
            max_reworks=1,
        ),
        StepContract(
            step_id="sql_generate",
            title="生成 SQL",
            summary="对照数据的结构写出查询，并说明每一列的口径",
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
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("sql",),
                    message="缺查询语句",
                    label="有查询语句",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="sql",
                    message="查询语句为空",
                    label="查询语句不是空的",
                ),
            ),
            max_reworks=2,
        ),
        StepContract(
            step_id="sql_execute",
            title="执行 SQL",
            summary="执行查询，原样带回结果和数据版本",
            input_refs=("sql",),
            method_text="Run the SQL with the run_sql tool. Return rows verbatim (max 200) and the data version reported by the tool. "
            + JSON_ONLY,
            tools=("run_sql",),
            output_artifact="rows",
            output_schema={"rows": [{}], "row_count": 0, "data_version": "string"},
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("rows", "data_version"),
                    message="缺结果或数据版本",
                    label="有结果和数据版本",
                ),
            ),
            on_reject="back",
            back_to="sql_generate",
            max_reworks=1,
        ),
        StepContract(
            step_id="normalize",
            title="整理结果",
            summary="统一单位、币种、期间的写法，标出缺的值",
            input_refs=("rewritten", "rows"),
            method_text="Normalize rows into the answer table: units, currency, period labels; flag missing values. "
            + JSON_ONLY,
            output_artifact="table",
            output_schema={"table": [{}], "notes": ["string"]},
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("table",),
                    message="缺整理后的表",
                    label="有整理后的表",
                ),
            ),
            max_reworks=1,
        ),
        StepContract(
            step_id="final",
            title="成稿",
            summary="写出答案：每个数带数据版本和口径，写明局限；结论栏留空",
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
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("answer", "citations"),
                    message="缺回答或出处",
                    label="有回答和出处",
                ),
                AcceptanceRule(
                    kind="no_positioning_advice",
                    path="answer",
                    message="含有评级、目标价或买卖建议的措辞",
                    label="没有评级、目标价、买卖建议",
                ),
            ),
            on_reject="human",
            max_reworks=1,
        ),
    ),
    answers={
        "解决什么": "对已入库数据集的口径明确的数值问题",
        "不解决什么": "预测、评级、买卖建议",
        "输入": "用户的问题，其中写明要查的指标与期间",
        "输出": "答案表：每个数带数据版本与口径，附局限；结论栏留空",
        "谁做判断": "用户。专家只取数、说明口径",
        "失败怎么办": "查询带不回结果时退回重写查询；成稿不合格时交人处理",
    },
)

# ---------------------------------------------------------------- 财报体检
# 先确认数据能不能用（摸底、勾稽），再取数、算指标、对照官方，最后成稿。
# 勾稽与对照这两步不给返工额度：不合格直接交人。返工的提示会催模型「按格式重交」，
# 在这两步上等于催它把不平的数改成平的。
_FIN_INPUT_CHARS = 24000
_FIN_DATASET = (
    "Every knowledge tool call in this step MUST pass the argument "
    "dataset=<profile.dataset>; never query the default dataset. "
)
_FIN_SQL = (
    "Write SQL in the dialect stated in the description of the run_sql tool and "
    "use table names alone, without schema or database prefix. "
)
_FIN_NUMBERS = (
    "Use only values returned by the tools in this step or given in the input "
    "artifacts; never recall or estimate a number. "
)

FIN_REVIEW = ExpertPack(
    pack_id="FIN_REVIEW",
    version="1",
    profile_id="FIN_REVIEW",
    title="财报体检（先验数据，再算指标，不下结论）",
    name="财报体检",
    tagline="先验数据，再算指标，不下结论",
    requires_tools=True,
    dossier=(
        DossierBlock(
            key="answer",
            title="回答",
            kind="text",
            artifact="note",
            path="answer",
            section="answer",
        ),
        # 两张表取各自那一步验过的交回物，不取成稿里模型重抄的那一份
        DossierBlock(
            key="metrics",
            title="指标表",
            kind="table",
            artifact="metrics",
            path="table",
            section="data",
            also={"data_version": "data_version"},
            columns=(
                ("display_name", "指标"),
                ("metric_name", "指标代码"),
                ("fiscal_year", "年度"),
                ("report_type", "报告"),
                ("basis", "口径"),
                ("value", "值"),
                ("unit", "单位"),
                ("applicable", "适用"),
                ("reason_if_not", "不适用的原因"),
                ("formula", "算法"),
                ("source", "来自"),
            ),
        ),
        DossierBlock(
            key="facts",
            title="取数表",
            kind="table",
            artifact="facts",
            path="table",
            section="data",
            folded=True,
            also={"data_version": "data_version"},
            columns=(
                ("display_name", "科目"),
                ("item", "字段"),
                ("fiscal_year", "年度"),
                ("report_type", "报告"),
                ("report_date", "报告期"),
                ("basis", "口径"),
                ("value", "值"),
                ("unit", "单位"),
                ("statement", "报表"),
            ),
        ),
        DossierBlock(
            key="reconcile",
            title="勾稽",
            kind="checks",
            artifact="reconcile",
            path="checks",
            section="verified",
            also={
                "continuity": "continuity",
                "unexplained_breaks": "unexplained_breaks",
            },
            columns=(
                ("rule_id", "编号"),
                ("rule", "规则"),
                ("periods_checked", "验了几期"),
                ("unbalanced", "不平的期数"),
                ("unbalanced_periods", "不平的期"),
            ),
        ),
        DossierBlock(
            key="crosscheck",
            title="对照官方",
            kind="coverage",
            artifact="crosscheck",
            section="verified",
        ),
        DossierBlock(
            key="mismatched",
            title="和公司披露的数对不上的",
            kind="table",
            artifact="crosscheck",
            path="mismatched",
            section="verified",
            columns=(
                ("fiscal_year", "年度"),
                ("item", "字段"),
                ("basis", "口径"),
                ("official", "公司披露的"),
                ("dataset", "数据集里的"),
                ("source_report", "出自"),
                ("page", "页"),
            ),
        ),
        DossierBlock(
            key="not_covered",
            title="没有对照到的",
            kind="table",
            artifact="crosscheck",
            path="not_covered",
            section="verified",
            columns=(("fiscal_year", "年度"), ("report_type", "报告")),
        ),
        DossierBlock(
            key="matched",
            title="和公司披露的数一致的",
            kind="table",
            artifact="crosscheck",
            path="matched",
            section="verified",
            folded=True,
            columns=(
                ("fiscal_year", "年度"),
                ("item", "字段"),
                ("basis", "口径"),
                ("value", "值"),
                ("source_report", "出自"),
                ("page", "页"),
            ),
        ),
        DossierBlock(
            key="observations",
            title="观察",
            kind="list",
            artifact="note",
            path="observations",
            section="notes",
        ),
        DossierBlock(
            key="caveats",
            title="提醒",
            kind="list",
            artifact="note",
            path="caveats",
            section="notes",
        ),
        DossierBlock(
            key="limitations",
            title="局限",
            kind="list",
            artifact="note",
            path="limitations",
            section="limits",
        ),
        DossierBlock(
            key="citations",
            title="出处",
            kind="list",
            artifact="note",
            path="citations",
            section="sources",
        ),
    ),
    workflow=(
        StepContract(
            step_id="scope",
            title="定范围",
            summary="认出公司、期间、报告类型",
            method_text=(
                "Rewrite the user's question into the scope of a financial statement "
                "review of ONE A-share listed company: the six-digit security code, the "
                "periods (fiscal years, or report dates for interim reports), the report "
                "types (年报, 中报, 一季报, 三季报) and the aspects asked about. "
                "If the question names no period, use the five most recent fiscal years "
                "and say so in `assumptions`. If the security code cannot be determined "
                "from the question, leave `security_code` empty; do not guess. "
                "Do not call any tool in this step. " + JSON_ONLY
            ),
            output_artifact="scope",
            output_schema={
                "company": "string",
                "security_code": "six digits",
                "periods": ["2023", "2024", "2025"],
                "report_types": ["年报"],
                "aspects": ["string"],
                "assumptions": ["string"],
                "question": "string",
            },
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("security_code", "periods", "report_types"),
                    message="缺证券代码、期间或报告类型",
                    label="有证券代码、期间、报告类型",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="security_code",
                    message="问题里认不出证券代码",
                    label="认得出是哪家公司",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="periods",
                    min_items=1,
                    message="期间为空",
                    label="期间不是空的",
                ),
            ),
            on_reject="human",
            max_reworks=1,
        ),
        StepContract(
            step_id="profile",
            title="数据摸底",
            summary="确认有这家公司的数据、数据到哪一天、各期是什么口径",
            input_refs=("scope",),
            method_text=(
                "Find the dataset and describe what it can support, before any number "
                "is used. 1) Call list_datasets and pick the dataset whose "
                "security_code equals scope.security_code. If there is none, the "
                "company has not been ingested: set `dataset` to null and "
                "`not_ingested` to true and stop; never pick another dataset and never "
                "use the default dataset. 2) With dataset=<that dataset> on every call: "
                "read dataset_metadata (all rows) and copy every note that matters for "
                "the periods in scope into `notes`, verbatim; read the statement rows "
                "in scope and list for each period its `basis` (原始披露, 追溯调整后 or "
                "未核实) and `verified`; read disclosure_calendar for the official "
                "disclosure date of each report. 3) List in `basis_breaks` every pair "
                "of adjacent periods in scope whose basis differs. "
                "Disclosure dates come ONLY from disclosure_calendar; the column "
                "aggregator_notice_date in statement rows is a third-party date and "
                "must not be reported as the disclosure date. " + JSON_ONLY
            ),
            tools=("list_datasets", "describe_schema", "metric_definitions", "run_sql"),
            output_artifact="profile",
            output_schema={
                "dataset": "string or null",
                "not_ingested": False,
                "security_code": "string",
                "data_version": "string, as reported by the tools",
                "as_of": "string, as reported by the tools",
                "notes": ["verbatim notes from dataset_metadata"],
                "periods": [
                    {
                        "fiscal_year": 2025,
                        "report_type": "年报",
                        "report_date": "2025-12-31",
                        "basis": "原始披露",
                        "verified": True,
                        "official_disclosed_date": "string or null",
                    }
                ],
                "basis_breaks": [
                    {"between": ["2020", "2021"], "basis": ["原始披露", "追溯调整后"]}
                ],
            },
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("dataset", "data_version", "periods", "basis_breaks"),
                    message="缺数据集、数据版本、期间或口径断点",
                    label="有数据集、数据版本、期间、口径断点",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="dataset",
                    message="这家公司未入库：没有对应的数据集",
                    label="有对应的数据集",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="data_version",
                    message="没有数据版本",
                    label="有数据版本",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="periods",
                    min_items=1,
                    message="范围内没有任何一期的数据",
                    label="范围内至少有一期数据",
                ),
            ),
            on_reject="human",
            max_reworks=1,
        ),
        StepContract(
            step_id="reconcile",
            title="勾稽",
            summary="三张表之间、前后两年之间对不对得上",
            input_refs=("scope", "profile"),
            method_text=(
                "Check that the statements in scope are internally consistent before "
                "anything is computed from them. " + _FIN_DATASET + "1) Read the table "
                "reconciliation_rules. For EVERY rule run its check over every period "
                "in scope and report one entry in `checks`: how many periods were "
                "checked and how many were unbalanced (absolute difference above the "
                "tolerance given by the rule table, 1 yuan if none is given). "
                "2) Cross-period continuity: for each pair of consecutive annual "
                "reports in scope, compare the opening cash balance with the closing "
                "cash balance of the previous year; list every pair that differs in "
                "`continuity` with the difference. 3) For each entry in `continuity`, "
                "look for the explanation in profile.notes. If the notes explain it, "
                "quote the note in `note`. If they do not, add the fiscal year to "
                "`unexplained_breaks`. Report what the queries return. An unbalanced "
                "check is a finding, not a mistake to correct: never adjust, round "
                "away or omit a difference. " + _FIN_SQL + _FIN_NUMBERS + JSON_ONLY
            ),
            tools=("run_sql",),
            output_artifact="reconcile",
            output_schema={
                "checks": [
                    {
                        "rule_id": "R01",
                        "rule": "string",
                        "periods_checked": 5,
                        "unbalanced": 0,
                        "unbalanced_periods": ["report_date"],
                    }
                ],
                "continuity": [
                    {"fiscal_year": 2021, "diff": 0.0, "note": "quoted from the notes"}
                ],
                "unexplained_breaks": [],
                "data_version": "string",
            },
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("checks", "continuity", "unexplained_breaks"),
                    message="缺勾稽结果、跨期连续性或未解释的断点",
                    label="有勾稽结果、跨期连续性、未解释的断点",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="checks",
                    min_items=1,
                    message="没有做勾稽",
                    label="做了勾稽",
                ),
                AcceptanceRule(
                    kind="all_equal",
                    path="checks",
                    field="unbalanced",
                    equals=0,
                    message="有勾稽规则不平，不往下算",
                    label="每条勾稽规则都平",
                ),
                AcceptanceRule(
                    kind="list_empty",
                    path="unexplained_breaks",
                    message="有跨期断点在数据集说明里找不到解释",
                    label="跨期的断点都有解释",
                ),
            ),
            on_reject="human",
            max_reworks=0,
            input_max_chars=_FIN_INPUT_CHARS,
        ),
        StepContract(
            step_id="extract",
            title="取数",
            summary="把问题要用到的报表科目按期取出来，每个数带口径",
            input_refs=("scope", "profile"),
            method_text=(
                "Extract the statement items needed for the aspects in scope, for the "
                "periods in scope. " + _FIN_DATASET + "Use describe_schema and the "
                "table field_dictionary for the meaning and unit of each column. Every "
                "row of `table` is one number and carries the basis of the statement "
                "row it was read from. At most 120 rows: keep the items the question "
                "needs. A value that is NULL in the dataset is reported as null, never "
                "as 0. " + _FIN_SQL + _FIN_NUMBERS + JSON_ONLY
            ),
            tools=("describe_schema", "run_sql"),
            output_artifact="facts",
            output_schema={
                "table": [
                    {
                        "fiscal_year": 2025,
                        "report_type": "年报",
                        "report_date": "2025-12-31",
                        "basis": "原始披露",
                        "statement": "income_statement",
                        "item": "operate_income",
                        "display_name": "营业收入",
                        "value": 0.0,
                        "unit": "元",
                    }
                ],
                "sql": ["every SQL statement that was run"],
                "data_version": "string",
            },
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("table", "data_version"),
                    message="缺取数表或数据版本",
                    label="有取数表和数据版本",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="table",
                    min_items=1,
                    message="取数表为空",
                    label="取数表不是空的",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="data_version",
                    message="没有数据版本",
                    label="有数据版本",
                ),
            ),
            on_reject="back",
            back_to="profile",
            max_reworks=1,
            input_max_chars=_FIN_INPUT_CHARS,
        ),
        StepContract(
            step_id="metrics",
            title="算指标",
            summary="按数据集里登记的口径算指标；分母不成立或口径不同的，标为不适用",
            input_refs=("scope", "profile", "facts"),
            method_text=(
                "Compute the metrics the question needs, using ONLY definitions "
                "returned by metric_definitions for this dataset. "
                + _FIN_DATASET
                + "1) Call metric_definitions. 2) For every needed metric whose "
                "definition says queryable=true, and when the tool query_metric is "
                "available, get the values with query_metric (metrics by name, "
                "filters on report_type and fiscal_year) and copy value, applicable "
                "and reason exactly as returned; set `source` to query_metric. Do not "
                "recompute these yourself. 3) For the other metrics, or when "
                "query_metric is not available, compute with run_sql following the "
                "definition; set `source` to run_sql and give the formula and the "
                "inputs. A ratio whose denominator is negative or within 1 yuan of "
                "zero is NOT applicable: set `applicable` to false, `value` to null and "
                "give the reason. A metric that needs two periods (an average of "
                "opening and closing balances, growth, a difference of ratios) is "
                "computed only when both periods have the same basis in "
                "profile.periods; otherwise set `applicable` to false with the reason "
                "口径不同. Ratios are reported as returned (0.25 means 25%); do not "
                "rescale. Do not state whether a value is good or bad. "
                + _FIN_SQL
                + _FIN_NUMBERS
                + JSON_ONLY
            ),
            tools=("metric_definitions", "query_metric", "run_sql"),
            output_artifact="metrics",
            output_schema={
                "table": [
                    {
                        "fiscal_year": 2025,
                        "report_type": "年报",
                        "basis": "原始披露",
                        "metric_name": "gross_margin",
                        "display_name": "毛利率",
                        "value": 0.0,
                        "unit": "比率",
                        "formula": "string, from the metric definition",
                        "inputs": {"operate_income": 0.0, "operate_cost": 0.0},
                        "applicable": True,
                        "reason_if_not": "",
                        "source": "query_metric or run_sql",
                    }
                ],
                "sql": ["every SQL statement that was run"],
                "data_version": "string",
            },
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("table", "data_version"),
                    message="缺指标表或数据版本",
                    label="有指标表和数据版本",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="table",
                    min_items=1,
                    message="指标表为空",
                    label="指标表不是空的",
                ),
            ),
            on_reject="rework",
            max_reworks=2,
            input_max_chars=_FIN_INPUT_CHARS,
        ),
        StepContract(
            step_id="crosscheck",
            title="对照官方",
            summary="拿取到的数和公司年报里自己披露的数逐项对照",
            input_refs=("scope", "profile", "facts"),
            method_text=(
                "Compare the key items in facts with the figures the company itself "
                "published. " + _FIN_DATASET + "The table official_key_figures holds "
                "figures read from the annual reports, each with its basis, source "
                "report, disclosure date and page. For every annual period in scope "
                "and every item present in both places, compare the value in facts "
                "with the official figure OF THE SAME BASIS (tolerance 1 yuan). List "
                "agreements in `matched` and disagreements in `mismatched`, each with "
                "source report and page. A period with no official figure of the same "
                "basis is listed in `not_covered`, not in `mismatched`. `coverage` says "
                "in one sentence how many of the periods in scope were compared. A "
                "disagreement is a finding: never alter a value to make it agree. "
                + _FIN_SQL
                + _FIN_NUMBERS
                + JSON_ONLY
            ),
            tools=("run_sql",),
            output_artifact="crosscheck",
            output_schema={
                "matched": [
                    {
                        "fiscal_year": 2025,
                        "item": "operate_income",
                        "basis": "原始披露",
                        "value": 0.0,
                        "source_report": "string",
                        "page": 6,
                    }
                ],
                "mismatched": [
                    {
                        "fiscal_year": 2025,
                        "item": "string",
                        "basis": "string",
                        "official": 0.0,
                        "dataset": 0.0,
                        "source_report": "string",
                        "page": 6,
                    }
                ],
                "not_covered": [{"fiscal_year": 2026, "report_type": "中报"}],
                "coverage": "string",
                "data_version": "string",
            },
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("matched", "mismatched", "coverage"),
                    message="缺一致项、不一致项或覆盖说明",
                    label="有一致项、不一致项、覆盖说明",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="coverage",
                    message="没有说明对照的覆盖范围",
                    label="说明了对照覆盖了哪些期",
                ),
                AcceptanceRule(
                    kind="list_empty",
                    path="mismatched",
                    message="有数字与公司披露的原文对不上，不往下写",
                    label="和公司披露的数都对得上",
                ),
            ),
            on_reject="human",
            max_reworks=0,
            input_max_chars=_FIN_INPUT_CHARS,
        ),
        StepContract(
            step_id="note",
            title="成稿",
            summary="只用前面各步的结果写底稿：列事实和提醒，结论栏留空",
            input_refs=(
                "scope",
                "profile",
                "reconcile",
                "facts",
                "metrics",
                "crosscheck",
            ),
            method_text=(
                "Write the working paper in Chinese from the input artifacts only; do "
                "not call any tool. `answer` answers the user's question with the "
                "numbers from facts and metrics, each with its period and basis. "
                "`observations` state facts and the direction of change only; no "
                "evaluation words such as 好, 差, 优秀, 值得, 健康. `caveats` MUST "
                "contain: every basis break in profile.basis_breaks that lies within "
                "or next to the periods used, with its explanation quoted from "
                "profile.notes; every period whose basis is 未核实; every metric "
                "marked not applicable, with the reason; every entry of "
                "reconcile.continuity; every accounting note in profile.notes that "
                "affects comparability of the periods used. If there is nothing of a "
                "kind, do not invent one; if there is no caveat at all, say "
                "'本次范围内没有口径断点、未核实期间或不适用的指标' as the only caveat. "
                "State an accounting fact only when profile.notes states it; never "
                "from your own knowledge. `citations` name the dataset, the data "
                "version, and for official figures the source report and page. "
                "No forecast, valuation, rating, target price, or advice to buy, sell "
                "or hold: when the question asks for one, `answer` says that this "
                "review does not give investment advice and lists what the data shows. "
                "`conclusion` stays an empty string: the user writes the conclusion. "
                + JSON_ONLY
            ),
            output_artifact="note",
            output_schema={
                "answer": "string",
                "tables": [{"title": "string", "rows": [{}]}],
                "observations": ["string"],
                "caveats": ["string"],
                "limitations": ["string"],
                "citations": ["string"],
                "data_version": "string",
                "conclusion": "",
            },
            acceptance=(
                AcceptanceRule(
                    kind="json_object",
                    message="交回物不是 JSON 对象",
                    label="交回的格式对",
                ),
                AcceptanceRule(
                    kind="required_keys",
                    keys=("answer", "caveats", "citations", "data_version"),
                    message="缺回答、提醒、出处或数据版本",
                    label="有回答、提醒、出处、数据版本",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="answer",
                    message="回答为空",
                    label="回答不是空的",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="caveats",
                    min_items=1,
                    message="没有提醒",
                    label="有提醒",
                ),
                AcceptanceRule(
                    kind="list_min",
                    path="citations",
                    min_items=1,
                    message="没有出处",
                    label="有出处",
                ),
                AcceptanceRule(
                    kind="non_empty",
                    path="data_version",
                    message="没有数据版本",
                    label="有数据版本",
                ),
                AcceptanceRule(
                    kind="blank",
                    path="conclusion",
                    message="结论栏必须留空",
                    label="结论栏是空的",
                ),
                AcceptanceRule(
                    kind="no_positioning_advice",
                    message="含有评级、目标价或买卖建议的措辞",
                    label="没有评级、目标价、买卖建议",
                ),
                AcceptanceRule(
                    kind="max_chars",
                    max_chars=30000,
                    message="底稿过长",
                    label="底稿没有超长",
                ),
            ),
            on_reject="human",
            max_reworks=1,
            input_max_chars=_FIN_INPUT_CHARS,
        ),
    ),
    answers={
        "解决什么": "对一家已入库的 A 股非金融公司，按指定期间给出带出处、带口径、先过勾稽的财务体检底稿",
        "不解决什么": "预测、估值、评级、买卖或仓位建议；没有入库的公司；附注与经营数据",
        "输入": "用户的问题，其中含公司与期间",
        "输出": "底稿：数据摸底、勾稽结果、取数表、指标表、与官方的对照、观察与提醒；结论栏留空",
        "谁做判断": "用户。专家只列事实、算指标、提示口径问题",
        "失败怎么办": "公司未入库、勾稽不平、与官方对不上时，不往下算，交人处理",
    },
)

BUILTIN_PACKS: dict[str, ExpertPack] = {
    p.profile_id: p for p in (SMOKE, DATA_QUERY, FIN_REVIEW)
}


def listed_packs() -> list[ExpertPack]:
    """给用户看的专家。只给我们自己测机制用的不在里面。"""
    return [p for p in BUILTIN_PACKS.values() if not p.internal]


def find_pack(profile_id: str, version: str | None = None) -> ExpertPack | None:
    pack = BUILTIN_PACKS.get(profile_id)
    if pack is None or (version not in (None, "", pack.version)):
        return None
    return pack
