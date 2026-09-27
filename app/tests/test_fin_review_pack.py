"""财报体检专家包（0008-info 段四）的结构与各步验收：纯数据与纯函数，不需要数据库。"""

from __future__ import annotations

import json

import pytest

from app.application.workbench.acceptance import judge
from app.application.workbench.advisor import Advisor
from app.domain.workbench.packs import (
    BUILTIN_PACKS,
    DATA_QUERY,
    FIN_REVIEW,
    SMOKE,
    find_pack,
)

KNOWLEDGE_TOOLS = {
    "list_datasets",
    "describe_schema",
    "metric_definitions",
    "query_metric",
    "run_sql",
}
STEPS = ["scope", "profile", "reconcile", "extract", "metrics", "crosscheck", "note"]


def step(step_id: str):
    return FIN_REVIEW.workflow[FIN_REVIEW.index_of(step_id)]


def accepted(step_id: str, content) -> bool:
    return judge(json.dumps(content, ensure_ascii=False), step(step_id).acceptance).ok


def failures(step_id: str, content) -> list[str]:
    text = content if isinstance(content, str) else json.dumps(content)
    return judge(text, step(step_id).acceptance).failures


SCOPE = {
    "company": "上海机场",
    "security_code": "600009",
    "periods": ["2023", "2024", "2025"],
    "report_types": ["年报"],
    "aspects": ["收入", "利润"],
    "question": "…",
}
PROFILE = {
    "dataset": "sh600009-financials",
    "not_ingested": False,
    "security_code": "600009",
    "data_version": "sh600009-financials-39a395bfa6f16b67",
    "as_of": "2026-06-30",
    "notes": ["2021 年年报行为追溯调整后口径"],
    "periods": [
        {
            "fiscal_year": 2025,
            "report_type": "年报",
            "basis": "原始披露",
            "verified": True,
            "official_disclosed_date": "2026-03-28",
        }
    ],
    "basis_breaks": [],
}
RECONCILE = {
    "checks": [
        {"rule_id": f"R0{i}", "rule": "…", "periods_checked": 3, "unbalanced": 0}
        for i in range(1, 10)
    ],
    "continuity": [],
    "unexplained_breaks": [],
    "data_version": PROFILE["data_version"],
}
FACTS = {
    "table": [
        {
            "fiscal_year": 2025,
            "report_type": "年报",
            "basis": "原始披露",
            "item": "operate_income",
            "value": 1.0,
            "unit": "元",
        }
    ],
    "data_version": PROFILE["data_version"],
}
METRICS = {
    "table": [
        {
            "fiscal_year": 2025,
            "metric_name": "gross_margin",
            "value": 0.3,
            "applicable": True,
        }
    ],
    "data_version": PROFILE["data_version"],
}
CROSSCHECK = {
    "matched": [{"fiscal_year": 2025, "item": "operate_income"}],
    "mismatched": [],
    "not_covered": [],
    "coverage": "范围内 3 期年报全部对照",
    "data_version": PROFILE["data_version"],
}
NOTE = {
    "answer": "2025 年营业收入 1 元（原始披露）。",
    "tables": [],
    "observations": ["营业收入较上年增加"],
    "caveats": ["本次范围内没有口径断点、未核实期间或不适用的指标"],
    "limitations": ["不含附注"],
    "citations": ["sh600009-financials，数据版本 sh600009-financials-39a395bfa6f16b67"],
    "data_version": PROFILE["data_version"],
    "conclusion": "",
}
GOOD = {
    "scope": SCOPE,
    "profile": PROFILE,
    "reconcile": RECONCILE,
    "extract": FACTS,
    "metrics": METRICS,
    "crosscheck": CROSSCHECK,
    "note": NOTE,
}


# ---------------------------------------------------------------- 结构


def test_the_pack_is_registered_next_to_the_existing_ones():
    assert set(BUILTIN_PACKS) == {"SMOKE", "DATA_QUERY", "FIN_REVIEW"}
    assert find_pack("FIN_REVIEW") is FIN_REVIEW
    assert find_pack("FIN_REVIEW", "1") is FIN_REVIEW
    assert find_pack("FIN_REVIEW", "2") is None
    assert BUILTIN_PACKS["SMOKE"] is SMOKE and BUILTIN_PACKS["DATA_QUERY"] is DATA_QUERY


def test_seven_steps_with_checks_before_any_number_is_used():
    assert [s.step_id for s in FIN_REVIEW.workflow] == STEPS
    order = {s: i for i, s in enumerate(STEPS)}
    assert order["profile"] < order["reconcile"] < order["extract"] < order["metrics"]
    assert FIN_REVIEW.requires_tools
    assert set(FIN_REVIEW.answers) == {
        "解决什么",
        "不解决什么",
        "输入",
        "输出",
        "谁做判断",
        "失败怎么办",
    }


def test_every_input_was_produced_by_an_earlier_step():
    produced: set[str] = set()
    for s in FIN_REVIEW.workflow:
        assert set(s.input_refs) <= produced, s.step_id
        assert s.output_artifact not in produced
        produced.add(s.output_artifact)
        if s.on_reject == "back":
            assert FIN_REVIEW.index_of(s.back_to) < FIN_REVIEW.index_of(s.step_id)


def test_only_knowledge_tools_are_named_and_only_where_data_is_read():
    for s in FIN_REVIEW.workflow:
        assert set(s.tools) <= KNOWLEDGE_TOOLS, s.step_id
    assert step("scope").tools == () and step("note").tools == ()
    assert "list_datasets" in step("profile").tools
    assert [s.step_id for s in FIN_REVIEW.workflow if "list_datasets" in s.tools] == [
        "profile"
    ]


def test_the_two_gates_go_to_a_human_without_any_rework():
    """返工的提示催模型按格式重交；在这两步上等于催它把不平的数改成平的。"""
    for name in ("reconcile", "crosscheck"):
        assert (step(name).on_reject, step(name).max_reworks) == ("human", 0), name
    assert (step("extract").on_reject, step("extract").back_to) == ("back", "profile")
    assert step("note").on_reject == "human"


def test_every_step_that_reads_data_is_told_to_name_the_dataset():
    for name in ("reconcile", "extract", "metrics", "crosscheck"):
        text = step(name).method_text
        assert "dataset=<profile.dataset>" in text, name
        assert "never recall or estimate a number" in text, name
        assert "dialect stated in the description of the run_sql tool" in text, name
    assert "never pick another dataset" in step("profile").method_text
    assert "aggregator_notice_date" in step("profile").method_text
    assert "`conclusion` stays an empty string" in step("note").method_text


def test_metrics_are_taken_by_name_when_the_tool_is_there():
    text = step("metrics").method_text
    assert step("metrics").tools == ("metric_definitions", "query_metric", "run_sql")
    assert "queryable=true" in text and "when the tool query_metric is" in text
    assert "copy value, applicable" in text and "Do not recompute" in text
    assert "when query_metric is not available, compute with run_sql" in text
    assert "do not rescale" in text
    # 别的步骤不给这个工具：取数与勾稽要的是报表里的数，不是算出来的口径
    assert [s.step_id for s in FIN_REVIEW.workflow if "query_metric" in s.tools] == [
        "metrics"
    ]


def test_the_pack_writes_no_judgement_rules():
    """F-POS-02：方法文本只规定查什么、按什么口径算、必须提醒什么，不规定多少算好。"""
    for s in FIN_REVIEW.workflow:
        lowered = s.method_text.lower()
        for phrase in ("is healthy", "is good when", "should exceed", "is risky"):
            assert phrase not in lowered, (s.step_id, phrase)
    assert "Do not state whether a value is good or bad" in step("metrics").method_text


def test_every_artifact_fits_the_turn_input_of_the_steps_that_read_it():
    for s in FIN_REVIEW.workflow:
        if "facts" in s.input_refs or "metrics" in s.input_refs:
            assert s.input_max_chars == 24000, s.step_id
    assert SMOKE.workflow[1].input_max_chars == 8000  # 原有专家包不变
    assert all(s.input_max_chars == 8000 for s in DATA_QUERY.workflow)


# ---------------------------------------------------------------- 各步的验收


@pytest.mark.parametrize("name", STEPS)
def test_a_well_formed_artifact_is_accepted(name):
    assert failures(name, GOOD[name]) == []


@pytest.mark.parametrize("name", STEPS)
def test_text_that_is_not_json_is_rejected(name):
    assert failures(name, "我查过了，没有问题。")


def test_scope_without_a_security_code_is_rejected():
    assert failures("scope", SCOPE | {"security_code": ""}) == [
        "non_empty(security_code): 问题里认不出证券代码"
    ]
    assert not accepted("scope", SCOPE | {"periods": []})
    assert not accepted("scope", {k: v for k, v in SCOPE.items() if k != "periods"})


def test_a_company_that_was_not_ingested_stops_at_profile():
    missing = PROFILE | {
        "dataset": None,
        "not_ingested": True,
        "data_version": "",
        "periods": [],
    }
    assert "non_empty(dataset): 这家公司未入库：没有对应的数据集" in failures(
        "profile", missing
    )
    assert not accepted("profile", PROFILE | {"periods": []})
    assert not accepted("profile", PROFILE | {"data_version": " "})


def test_one_unbalanced_rule_stops_the_review():
    checks = [dict(c) for c in RECONCILE["checks"]]
    checks[4]["unbalanced"] = 1
    assert failures("reconcile", RECONCILE | {"checks": checks}) == [
        "all_equal(checks): 有勾稽规则不平，不往下算"
    ]


def test_reconcile_cannot_pass_by_leaving_things_out():
    assert not accepted("reconcile", RECONCILE | {"checks": []})
    without = {k: v for k, v in RECONCILE.items() if k != "unexplained_breaks"}
    assert not accepted("reconcile", without)
    assert not accepted(
        "reconcile", RECONCILE | {"checks": [{"rule_id": "R01", "rule": "…"}]}
    )
    assert not accepted(
        "reconcile", RECONCILE | {"checks": [{"rule_id": "R01", "unbalanced": "0"}]}
    )


def test_an_explained_break_passes_and_an_unexplained_one_does_not():
    explained = RECONCILE | {
        "continuity": [
            {
                "fiscal_year": 2021,
                "diff": 1.5e9,
                "note": "2021 年年报行为追溯调整后口径",
            }
        ]
    }
    assert accepted("reconcile", explained)
    assert failures("reconcile", explained | {"unexplained_breaks": [2021]}) == [
        "list_empty(unexplained_breaks): 有跨期断点在数据集说明里找不到解释"
    ]


def test_a_figure_that_disagrees_with_the_report_stops_the_review():
    mismatch = {
        "fiscal_year": 2025,
        "item": "operate_income",
        "official": 2.0,
        "dataset": 1.0,
    }
    assert failures("crosscheck", CROSSCHECK | {"mismatched": [mismatch]}) == [
        "list_empty(mismatched): 有数字与公司披露的原文对不上，不往下写"
    ]
    without = {k: v for k, v in CROSSCHECK.items() if k != "mismatched"}
    assert not accepted("crosscheck", without)
    assert not accepted("crosscheck", CROSSCHECK | {"coverage": ""})


def test_interim_periods_without_official_figures_do_not_stop_the_review():
    interim = CROSSCHECK | {
        "matched": [],
        "not_covered": [{"fiscal_year": 2026, "report_type": "中报"}],
        "coverage": "范围内只有中报，没有可对照的官方数字",
    }
    assert accepted("crosscheck", interim)


def test_empty_tables_are_rejected():
    assert not accepted("extract", FACTS | {"table": []})
    assert not accepted("extract", FACTS | {"data_version": ""})
    assert not accepted("metrics", METRICS | {"table": []})


@pytest.mark.parametrize(
    ("change", "failure"),
    [
        ({"conclusion": "公司经营稳健"}, "blank(conclusion): 结论栏必须留空"),
        ({"caveats": []}, "list_min(caveats): 没有提醒"),
        ({"citations": []}, "list_min(citations): 没有出处"),
        ({"answer": " "}, "non_empty(answer): 回答为空"),
        ({"data_version": ""}, "non_empty(data_version): 没有数据版本"),
        (
            {"answer": "综合来看建议买入。"},
            "no_positioning_advice: 含投资建议措辞（F-POS-04）",
        ),
        (
            {"observations": ["目标价 40 元"]},
            "no_positioning_advice: 含投资建议措辞（F-POS-04）",
        ),
        (
            {"tables": [{"title": "t", "rows": [{"备注": "强烈推荐"}]}]},
            "no_positioning_advice: 含投资建议措辞（F-POS-04）",
        ),
    ],
)
def test_the_note_is_rejected_when(change, failure):
    assert failures("note", NOTE | change) == [failure]


def test_a_note_that_declines_to_advise_is_accepted():
    declined = NOTE | {
        "answer": "本体检不提供买卖或持有的意见。数据显示：2025 年营业收入 1 元。"
    }
    assert accepted("note", declined)


def test_a_note_without_the_conclusion_key_is_accepted_as_blank():
    assert accepted("note", {k: v for k, v in NOTE.items() if k != "conclusion"})


# ---------------------------------------------------------------- turn 输入


def test_the_turn_input_names_tools_inputs_and_shape():
    text = Advisor.compose(
        step("metrics"),
        {"text": "上海机场近三年毛利率"},
        {
            "scope": {"version": 1, "content": SCOPE},
            "profile": {"version": 2, "content": PROFILE},
            "facts": {"version": 1, "content": FACTS},
        },
        0,
    )
    assert "算指标 (metrics, v1)" in text
    assert "metric_definitions, query_metric, run_sql" in text
    assert "### profile (v2)" in text and "sh600009-financials" in text
    assert "Produce the artifact `metrics`" in text
    assert "TRUNCATED" not in text and "rework" not in text


def test_an_input_that_does_not_fit_is_marked_as_truncated():
    big = {"table": [{"item": f"item-{i}", "value": i} for i in range(3000)]}
    size = len(json.dumps(big, ensure_ascii=False))
    text = Advisor.compose(
        step("metrics"), {}, {"facts": {"version": 1, "content": big}}, 0
    )
    assert f"[TRUNCATED: `facts` is {size} characters, only the first 24000" in text
    assert "item-2999" not in text
    small = Advisor.compose(
        SMOKE.workflow[1], {}, {"plan": {"version": 1, "content": {"plan": ["x"]}}}, 0
    )
    assert "TRUNCATED" not in small
    text = Advisor.compose(
        SMOKE.workflow[1], {}, {"plan": {"version": 1, "content": big}}, 0
    )
    assert "only the first 8000" in text
