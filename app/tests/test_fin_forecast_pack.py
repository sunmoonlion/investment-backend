"""财务分析与预测专家包（k8s 仓 tree-build/SDD/modules/0023-fin-forecast.md 第五节）的结构与各步验收。

纯数据与纯函数，不需要数据库。交回物的样例用的是演示案例的数（1/20 样例上按 0023 的方法算出）。
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from app.application.workbench.acceptance import judge
from app.application.workbench.advisor import Advisor
from app.application.workbench.run_files import task_dossier
from app.domain.workbench.packs import BUILTIN_PACKS, FIN_FORECAST, FIN_REVIEW

KNOWLEDGE_TOOLS = {
    "list_datasets",
    "describe_schema",
    "metric_definitions",
    "query_metric",
    "run_sql",
}
STEPS = ["scope", "pull", "analyze", "forecast", "writeup"]
VERSION = "olist-ecommerce-dcca129e2ea0cb56"


def step(step_id: str):
    return FIN_FORECAST.workflow[FIN_FORECAST.index_of(step_id)]


def failures(step_id: str, content) -> list[str]:
    text = (
        content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    )
    return judge(text, step(step_id).acceptance).failures


SCOPE = {
    "question": "近两年按月收入怎么走？预测下两个季度。",
    "dataset": "olist-ecommerce",
    "data_version": VERSION,
    "period": {"start": "2017-01", "end": "2018-08", "grain": "month"},
    "complete_period": {"start": "2017-01", "end": "2018-08"},
    "metrics": [{"name": "gmv", "kind": "aggregate"}],
    "unavailable": [],
    "forecast": {"horizon": ["2018-Q3", "2018-Q4"]},
    "notes": ["2018-09 只有 1 单（已取消），之后没有数据，是数据截断，不是业务下滑"],
    "assumptions": ["「近两年」取数据说明里的完整月份"],
}
SERIES = {
    "columns": [
        {
            "name": "gmv",
            "display_name": "成交额",
            "source": "order_items.price + order_items.freight_value；剔除取消与不可用",
        }
    ],
    "rows": [
        {"period": "2017-01", "gmv": 5811.13},
        {"period": "2018-08", "gmv": 44372.02},
    ],
    "excluded_periods": [{"period": "2018-09", "reason": "数据截断"}],
    "sql": ["SELECT substr(o.order_purchase_timestamp, 1, 7) AS period, …"],
    "data_version": VERSION,
}
ANALYSIS = {
    "findings": [
        {"text": "2017 年爬坡", "numbers": ["5811.13", "31702.73"], "sql": "SELECT …"},
        {"text": "2018 年走平", "numbers": ["44372.02", "69707.02"], "sql": "SELECT …"},
    ],
    "data_version": VERSION,
}
FORECAST = {
    "method": "移动平均：最近 6 个完整月的均值，区间取这 6 个月的最低到最高",
    "window": {"start": "2018-03", "end": "2018-08", "count": 6},
    "excluded_periods": [{"period": "2018-09", "reason": "数据截断"}],
    "assumptions": ["近期水平延续", "没有新的大促、政策或口径变化"],
    "rows": [
        {
            "period": "2018-09",
            "forecast_point": 57264.69,
            "forecast_low": 44372.02,
            "forecast_high": 69707.02,
        }
    ],
    "quarters": [
        {
            "period": "2018-Q3",
            "forecast_point": 151098.28,
            "forecast_low": 138205.61,
            "forecast_high": 163540.61,
            "actual_months": ["2018-07", "2018-08"],
        }
    ],
    "backtest": {
        "window": {"start": "2017-09", "end": "2018-02"},
        "mape": 0.2162,
        "inside": 5,
        "total": 6,
    },
    "sql": ["WITH m AS (…) SELECT AVG(gmv) AS forecast_point …"],
    "data_version": VERSION,
}
NOTE = {
    "answer": "2017 年爬坡、2018 年走平；三季度约 15.1 万（13.8～16.4 万），四季度约 17.2 万。",
    "limitations": ["2018-09 起数据截断", "预测假设近期水平延续；回测平均误差约 22%"],
    "citations": [f"巴西电商 Olist 样例（1/20 抽样），数据版本 {VERSION}"],
    "data_version": VERSION,
}
GOOD = {
    "scope": SCOPE,
    "pull": SERIES,
    "analyze": ANALYSIS,
    "forecast": FORECAST,
    "writeup": NOTE,
}


# ---------------------------------------------------------------- 结构


def test_five_steps_in_the_order_the_demo_shows():
    assert [s.step_id for s in FIN_FORECAST.workflow] == STEPS
    assert [s.title for s in FIN_FORECAST.workflow] == [
        "定范围",
        "取数",
        "分析",
        "预测",
        "成稿",
    ]
    assert BUILTIN_PACKS["FIN_FORECAST"] is FIN_FORECAST
    assert FIN_FORECAST.deliverable_files() == ("底稿/财务分析与预测-成稿.md",)


def test_every_input_was_produced_by_an_earlier_step():
    produced: set[str] = set()
    for s in FIN_FORECAST.workflow:
        assert set(s.input_refs) <= produced, s.step_id
        produced.add(s.output_artifact)


def test_only_knowledge_tools_and_only_where_data_is_read():
    for s in FIN_FORECAST.workflow:
        assert set(s.tools) <= KNOWLEDGE_TOOLS, s.step_id
    assert set(step("scope").tools) == {
        "list_datasets",
        "describe_schema",
        "metric_definitions",
        "run_sql",
    }
    for step_id in ("pull", "analyze", "forecast"):
        assert "run_sql" in step(step_id).tools, step_id
    assert step("writeup").tools == ()


def test_each_step_has_its_own_route_after_a_rejection():
    """与财报体检、问数不同的编排：0023 第 5.2 节与 PPT 第 7 页。"""
    routes = {
        s.step_id: (s.on_reject, s.back_to, s.max_reworks, s.max_backs)
        for s in FIN_FORECAST.workflow
    }
    assert routes["scope"][:3] == ("human", None, 0)
    assert routes["pull"][:3] == ("rework", None, 1)
    assert routes["analyze"] == ("back", "pull", 0, 1)
    assert routes["forecast"][:3] == ("human", None, 0)
    assert routes["writeup"][:3] == ("human", None, 1)


def test_every_step_that_reads_data_is_told_which_dataset_and_how_to_name_columns():
    for step_id in ("pull", "analyze", "forecast"):
        text = step(step_id).method_text
        assert "dataset=<scope.dataset>" in text, step_id
        assert "metric_name" in text and "`period`" in text, step_id
        assert "never compute a number in your head" in text, step_id


def test_the_forecast_method_is_fixed_and_explainable():
    text = step("forecast").method_text
    for phrase in (
        "do not choose another method",
        "6 latest complete periods",
        "fewer than 3 exist, do not forecast",
        "at least 12 complete periods",
        "move the window back by 6 periods",
        "minimum and the maximum of the window",
        "forecast_point, forecast_low and forecast_high",
        "actual_months",
        "Never call a forecast certain",
    ):
        assert phrase in text, phrase


def test_missing_quantities_are_listed_not_replaced():
    text = step("scope").method_text
    assert "kind=unavailable" in text and "never substitute another field" in text
    assert "complete_start to complete_end" in text
    assert "`limitations` MUST" in step("writeup").method_text


def test_the_pack_writes_no_judgement_rules():
    """F-POS-02：方法只规定查什么、怎么算、必须写明什么，不规定多少算好。"""
    for s in FIN_FORECAST.workflow:
        lowered = s.method_text.lower()
        for phrase in ("is healthy", "is good when", "should exceed", "is risky"):
            assert phrase not in lowered, (s.step_id, phrase)
    assert "do not say whether something is good or bad" in step("analyze").method_text


def test_the_dossier_layout_still_has_every_section():
    sections = {b.section for b in FIN_FORECAST.dossier}
    assert {"answer", "data", "verified", "limits", "sources"} <= sections


# ---------------------------------------------------------------- 验收


@pytest.mark.parametrize("step_id", STEPS)
def test_a_well_formed_artifact_is_accepted(step_id):
    assert failures(step_id, GOOD[step_id]) == []


@pytest.mark.parametrize("step_id", STEPS)
def test_text_that_is_not_json_is_rejected(step_id):
    assert failures(step_id, "我做完了")[0].startswith("json_object")


def test_scope_without_a_dataset_stops_and_says_why():
    found = failures("scope", {**SCOPE, "dataset": None})
    assert found == ["non_empty(dataset): 没有找到能回答这个问题的数据集"]
    assert failures("scope", {**SCOPE, "metrics": []}) == [
        "list_min(metrics): 问题没有落到任何口径上"
    ]


@pytest.mark.parametrize("kind", ["row", "unknown"])
def test_scope_on_data_this_version_does_not_support_stops_and_says_why(kind):
    """2026-10-14 现网：「预测这个公司下个季度的表现」选中了 600009 的财报（口径是逐行的），取数超时。"""
    statement = {
        **SCOPE,
        "dataset": "sh600009-financials",
        "metrics": [{"name": "gross_margin", "kind": kind}],
    }
    found = failures("scope", statement)
    assert len(found) == 1 and found[0].startswith("all_equal(metrics):")
    assert "财报体检" in found[0]


def test_scope_asks_when_the_company_is_unclear():
    text = step("scope").method_text
    for phrase in (
        "kind=aggregate",
        "does not support financial statement datasets",
        "这个公司",
        "exactly one kept dataset fits, take it and say so in `assumptions`",
        "ask the user which one",
        "never finer than the data is stored in",
        "at most the latest 36 periods",
    ):
        assert phrase in text, phrase
    assert "never splitting data into a finer grain" in step("pull").method_text


def test_pull_needs_rows_and_the_sql_that_was_run():
    assert failures("pull", {**SERIES, "rows": []}) == ["list_min(rows): 取数结果为空"]
    assert failures("pull", {**SERIES, "sql": []}) == [
        "list_min(sql): 没有交回执行过的查询语句"
    ]


def test_one_finding_is_not_an_analysis():
    one = {**ANALYSIS, "findings": ANALYSIS["findings"][:1]}
    assert failures("analyze", one) == ["list_min(findings): 分析发现少于两条"]


def test_a_forecast_needs_two_assumptions_and_values():
    assert failures("forecast", {**FORECAST, "assumptions": ["近期水平延续"]}) == [
        "list_min(assumptions): 假设少于两条"
    ]
    assert failures("forecast", {**FORECAST, "rows": []}) == [
        "list_min(rows): 没有预测值"
    ]
    without = {k: v for k, v in FORECAST.items() if k != "backtest"}
    assert failures("forecast", without)[0].startswith("required_keys")


@pytest.mark.parametrize(
    ("change", "failure"),
    [
        ({"answer": " "}, "non_empty(answer): 回答为空"),
        ({"limitations": []}, "list_min(limitations): 没有写局限"),
        ({"citations": []}, "list_min(citations): 没有出处"),
        (
            {"answer": "四季度会更好，建议买入。"},
            "no_positioning_advice: 含有评级、目标价或买卖建议的措辞",
        ),
    ],
)
def test_the_writeup_is_rejected_when(change, failure):
    assert failures("writeup", {**NOTE, **change}) == [failure]


# ---------------------------------------------------------------- turn 输入


def test_the_turn_input_names_tools_inputs_and_shape():
    text = Advisor.compose(
        step("forecast"),
        {"text": SCOPE["question"]},
        {
            "scope": {"version": 1, "content": SCOPE},
            "series": {"version": 1, "content": SERIES},
            "analysis": {"version": 1, "content": ANALYSIS},
        },
        0,
    )
    assert "预测 (forecast, v1)" in text
    assert "run_sql" in text
    assert "### series (v1)" in text and "olist-ecommerce" in text
    assert "Produce the artifact `forecast`" in text
    assert "TRUNCATED" not in text


def test_the_last_step_writes_the_file_and_the_handback():
    last = FIN_FORECAST.workflow[-1]
    text = Advisor.compose(last, {"text": "q"}, {}, 0, last_step=True)
    assert "`底稿/财务分析与预测-成稿.md`" in text and "## Handback file" in text
    assert "*** Add File:" in text and "never use `*** Update File`" in text
    # 成稿这一步要写文件：不能再叫它「不调任何工具」
    assert "do not call any tool" not in text and "do not call any data tool" in text


def test_the_files_go_into_this_requests_folder_and_the_task_list_names_them():
    folder = "底稿/对话-0ca14efb/2026-10-14-092623-财务分析与预测"
    last = FIN_FORECAST.workflow[-1]
    text = Advisor.compose(last, {"text": "q"}, {}, 0, last_step=True, folder=folder)
    assert f"`{folder}/财务分析与预测-成稿.md`" in text
    assert f"`{folder}/交回.md`" in text and "for this request only" in text
    task = {
        "profile_id": "FIN_FORECAST",
        "profile_version": FIN_FORECAST.version,
        "session_id": "0ca14efb-4af9-40ae-8066-bd48f1c1af84",
        "created_at": datetime(2026, 10, 14, 1, 26, 23, tzinfo=UTC),
    }
    assert task_dossier(task) == {
        "folder": folder,
        "expert": "财务分析与预测",
        "files": [f"{folder}/财务分析与预测-成稿.md", f"{folder}/交回.md"],
    }
    # 旧版本的委托（专家包对不上）不列：它们写在旧的固定路径上
    assert task_dossier({**task, "profile_version": "1"}) is None


def test_it_is_a_different_orchestration_from_the_financial_review():
    def routes(pack):
        return [(s.on_reject, s.max_reworks) for s in pack.workflow]

    assert len(FIN_FORECAST.workflow) == 5 and len(FIN_REVIEW.workflow) == 7
    assert routes(FIN_FORECAST) != routes(FIN_REVIEW)
    assert {s.step_id for s in FIN_FORECAST.workflow if s.max_reworks == 0} == {
        "scope",
        "analyze",
        "forecast",
    }
