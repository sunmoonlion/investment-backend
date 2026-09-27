"""财报体检十三题（0008-info 段四、0009-semantic 丙段，MVP-08、SEM-10）。

数据集是 info 按自述第二版重建的 600009 原件。不访问网络，不需要数据库。
"""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path

import pytest

from eval.cases import Case, CaveatCheck, TruthQuery, load_cases
from eval.judge import ArmOutput, judge, judge_caveats
from eval.report import render_markdown, summarize
from eval.run_eval import (
    BASELINE_PROMPT,
    answer_text,
    baseline_prompt,
    sqls_from_events,
)
from eval.truth import QueryFailed, TruthStore, fingerprint, project, same_result

DATASET = Path(__file__).parents[1] / "eval/fixtures/sh600009-financials-v2.dataset.bin"
VERSION = "sh600009-financials-9fd91db79529e208"
CASES = load_cases("fin_review_13")
BY_ID = {c.case_id: c for c in CASES}


@pytest.fixture(scope="module")
def sqlite_store() -> TruthStore:
    return TruthStore(DATASET)


@pytest.fixture(scope="module")
def duck_store() -> TruthStore:
    store = TruthStore(DATASET, dialect="duckdb")
    yield store
    store.close()


def arm(case_id: str, **changes) -> ArmOutput:
    base = {"data_versions": [VERSION], "cost": Decimal("0.1")}
    return ArmOutput(case_id, "pack", **(base | changes))


# ---------------------------------------------------------------- 案例集


def test_thirteen_cases_frozen_on_one_data_version(sqlite_store, duck_store):
    assert len(CASES) == 13 and len(BY_ID) == 13
    assert {c.data_snapshot_id for c in CASES} == {VERSION}
    assert sqlite_store.data_version() == duck_store.data_version() == VERSION
    assert sum(len(c.truth_queries) for c in CASES) == 15
    assert sum(len(c.caveat_checks) for c in CASES) == 16
    assert sorted(c.case_id for c in CASES if not c.truth_queries) == [
        "fin-cash-continuity",
        "fin-no-advice",
        "fin-reconcile-balance",
    ]
    ids = [q.query_id for c in CASES for q in c.truth_queries]
    assert len(set(ids)) == len(ids)


def test_the_twenty_retail_cases_are_untouched():
    retail = load_cases()
    assert len(retail) == 20
    assert all(not c.caveat_checks and not c.traps for c in retail)
    assert all(not q.distinct for c in retail for q in c.truth_queries)


def test_truth_columns_are_names_that_exist_in_the_dataset(sqlite_store):
    """候选的 SQL 要逐列对上真值；真值用自造的列名，两臂都必然判负。"""
    _, tables = sqlite_store.query("SELECT name FROM sqlite_master WHERE type='table'")
    known: set[str] = set()
    for table in tables:
        _, columns = sqlite_store.query(f'PRAGMA table_info("{table["name"]}")')
        known |= {c["name"] for c in columns}
    _, metrics = sqlite_store.query("SELECT metric_name FROM metric_dictionary")
    known |= {m["metric_name"] for m in metrics}
    for case in CASES:
        for q in case.truth_queries:
            assert set(q.expected_columns) <= known, q.query_id
            assert set(q.key_columns) <= set(q.expected_columns), q.query_id
            assert set(q.value_columns) <= set(q.expected_columns), q.query_id


def test_every_truth_query_gives_the_same_result_in_both_dialects(
    sqlite_store, duck_store
):
    for case in CASES:
        for q in case.truth_queries:
            columns, rows = sqlite_store.truth(q)
            other_columns, other_rows = duck_store.truth(q)
            assert rows, q.query_id
            ok, why = same_result(other_columns, other_rows, columns, rows, q)
            assert ok, (q.query_id, why)


def test_truth_values_are_the_ones_checked_by_hand(sqlite_store):
    def rows(case_id: str, query_id: str):
        q = next(q for q in BY_ID[case_id].truth_queries if q.query_id == query_id)
        _, found = sqlite_store.truth(q)
        return project(found, q.expected_columns, q.key_columns, distinct=q.distinct)

    assert rows("fin-2021-revenue-basis", "q-2021-official") == [
        {"basis": "原始披露", "value": 3727797262.22},
        {"basis": "追溯调整后", "value": 8154776878.02},
    ]
    assert rows("fin-2021-yoy-break", "q-yoy-statement") == [
        {"fiscal_year": 2020, "basis": "原始披露", "operate_income": 4303465087.94},
        {"fiscal_year": 2021, "basis": "追溯调整后", "operate_income": 8154776878.02},
    ]
    assert rows("fin-2021-yoy-break", "q-yoy-official") == [
        {"fiscal_year": 2020, "basis": "原始披露", "value": 4303465087.94},
        {"fiscal_year": 2021, "basis": "原始披露", "value": 3727797262.22},
    ]
    assert rows("fin-disclosure-date", "q-disc") == [
        {"fiscal_year": 2021, "official_disclosed_date": "2022-04-16"}
    ]
    shares = rows("fin-invest-income-share", "q-iis")
    assert [r["fiscal_year"] for r in shares if r["invest_income_share"] is None] == [
        2020,
        2021,
        2022,
    ]
    lease = rows("fin-leverage-2025", "q-lev-items")[0]
    debt = rows("fin-leverage-2025", "q-lev-debt")[0]["interest_bearing_debt"]
    assert lease["lease_liab"] == 18261048473.52
    assert debt == pytest.approx(18261048473.52 + 1282564567.61)


# ---------------------------------------------------------------- 去重后比对


OFFICIAL = next(
    q
    for q in BY_ID["fin-2021-revenue-basis"].truth_queries
    if q.query_id == "q-2021-official"
)


def test_the_same_fact_listed_in_several_reports_counts_once(duck_store):
    """同一个数在多份年报里出现；候选把出处也选出来时行数更多，事实相同。"""
    candidate = (
        "SELECT basis, value, source_report, page FROM official_key_figures "
        "WHERE fiscal_year=2021 AND item='operate_income'"
    )
    columns, rows = duck_store.query(candidate)
    assert len(rows) == 5
    truth_columns, truth_rows = duck_store.truth(OFFICIAL)
    assert same_result(columns, rows, truth_columns, truth_rows, OFFICIAL) == (True, "")
    strict = TruthQuery(**{**OFFICIAL.__dict__, "distinct": False})
    ok, why = same_result(columns, rows, truth_columns, truth_rows, strict)
    assert not ok and why == "row count 5 != 2"
    assert fingerprint(rows, OFFICIAL) == fingerprint(truth_rows, OFFICIAL)


def test_distinct_does_not_hide_a_missing_or_extra_fact(duck_store):
    truth_columns, truth_rows = duck_store.truth(OFFICIAL)
    columns, rows = duck_store.query(
        "SELECT basis, value FROM official_key_figures "
        "WHERE fiscal_year=2021 AND item='operate_income' AND basis='原始披露'"
    )
    ok, why = same_result(columns, rows, truth_columns, truth_rows, OFFICIAL)
    assert not ok and why == "row count 1 != 2"
    columns, rows = duck_store.query(
        "SELECT basis, value FROM official_key_figures "
        "WHERE fiscal_year IN (2020, 2021) AND item='operate_income'"
    )
    ok, _ = same_result(columns, rows, truth_columns, truth_rows, OFFICIAL)
    assert not ok


# ---------------------------------------------------------------- 两种方言


def test_a_query_written_for_the_semantic_layer_runs_in_its_dialect(
    sqlite_store, duck_store
):
    sql = (
        "SELECT fiscal_year, basis, (operate_income - operate_cost) / operate_income "
        "AS gross_margin FROM income_statement WHERE report_type = '年报' "
        "AND fiscal_year BETWEEN 2019 AND 2025 QUALIFY ROW_NUMBER() OVER "
        "(PARTITION BY fiscal_year ORDER BY report_date) = 1"
    )
    case = BY_ID["fin-gross-margin-trend"]
    assert judge(case, arm(case.case_id, sqls=[sql]), duck_store).quality == "pass"
    verdict = judge(case, arm(case.case_id, sqls=[sql]), sqlite_store)
    assert verdict.quality == "fail"
    assert any(r.startswith("sql#0 failed") for r in verdict.reasons)


def test_the_sql_reported_by_query_metric_covers_the_truth(duck_store):
    """按口径名查询回报的 SQL（知识服务生成的原样）在评测里重跑，覆盖对应的真值。"""
    sql = (
        'SELECT "income_statement"."security_code" AS "security_code", '
        '"income_statement"."report_date" AS "report_date", '
        '"income_statement"."report_type" AS "report_type", '
        '"income_statement"."fiscal_year" AS "fiscal_year", '
        '"income_statement"."basis" AS "basis", '
        '"income_statement"."verified" AS "verified", '
        'CASE WHEN COALESCE(("income_statement"."operate_profit" > 1), FALSE) THEN '
        '("income_statement"."invest_income" / "income_statement"."operate_profit") '
        'END AS "invest_income_share", '
        'COALESCE(("income_statement"."operate_profit" > 1), FALSE) '
        'AS "invest_income_share__applicable" FROM income_statement '
        """WHERE "income_statement"."report_type" = '年报' """
        'AND "income_statement"."fiscal_year" >= 2019 '
        'AND "income_statement"."fiscal_year" <= 2025 '
        'ORDER BY "income_statement"."security_code" ASC, '
        '"income_statement"."report_date" ASC'
    )
    case = BY_ID["fin-invest-income-share"]
    verdict = judge(
        case,
        arm(case.case_id, sqls=[sql], answer_text="2020 至 2022 年不适用"),
        duck_store,
    )
    assert (verdict.quality, verdict.citation, verdict.caveats) == (
        "pass",
        "pass",
        "pass",
    )
    assert verdict.coverage == {"q-iis": 0}


def test_a_failing_query_is_reported_the_same_way_in_both_dialects(
    sqlite_store, duck_store
):
    for store in (sqlite_store, duck_store):
        with pytest.raises(QueryFailed):
            store.query("SELECT nope FROM income_statement")
        with pytest.raises(QueryFailed):
            store.query("SELECT * FROM no_such_table")


def test_the_evaluation_copy_cannot_reach_outside(duck_store, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("x")
    for sql in (
        f"SELECT * FROM read_text('{secret}')",
        f"COPY (SELECT 1) TO '{tmp_path}/out.csv'",
        "SET enable_external_access=true",
    ):
        with pytest.raises(QueryFailed):
            duck_store.query(sql)
    assert not (tmp_path / "out.csv").exists()


def test_an_unknown_dialect_is_refused():
    with pytest.raises(ValueError, match="unknown dialect"):
        TruthStore(DATASET, dialect="postgres")  # type: ignore[arg-type]


# ---------------------------------------------------------------- 必须提醒


def test_a_check_needs_one_of_the_words_and_none_of_the_forbidden():
    check = CaveatCheck("k", "d", any_of=("租赁",), none_of=("明显改善",))
    assert check.passes("受新租赁准则影响，前后不可直接比较")
    assert not check.passes("比值上升")
    assert not check.passes("受租赁准则影响，盈利质量明显改善")
    assert CaveatCheck("k", "d", none_of=("目标价",)).passes("")
    assert CaveatCheck("k", "d").passes("anything")


@pytest.mark.parametrize(
    ("case_id", "text", "level", "missed"),
    [
        (
            "fin-2021-revenue-basis",
            "81.55 亿元，为追溯调整后口径，因 2022 年同一控制下企业合并。",
            "pass",
            [],
        ),
        ("fin-2021-revenue-basis", "81.55 亿元。", "fail", ["k-restated", "k-merger"]),
        ("fin-2021-revenue-basis", "为追溯调整后口径。", "fail", ["k-merger"]),
        (
            "fin-2021-yoy-break",
            "两年口径不同，不可直接比较；原始披露口径下同比 -13.4%。",
            "pass",
            [],
        ),
        (
            "fin-2021-yoy-break",
            "同比增长 89.5%。",
            "fail",
            ["k-break", "k-original-yoy"],
        ),
        ("fin-disclosure-date", "披露日为 2022-04-16。", "pass", []),
        ("fin-disclosure-date", "公告日为 2022-02-26。", "fail", ["k-official-date"]),
        ("fin-no-advice", "本体检不提供买卖建议。数据显示收入回升。", "pass", []),
        ("fin-no-advice", "不提供评级，但给出目标价 40 元。", "fail", ["k-no-advice"]),
        ("fin-no-advice", "基本面改善。", "fail", ["k-declines"]),
        (
            "fin-ocf-quality",
            "受新租赁准则影响；盈利质量明显改善。",
            "fail",
            ["k-no-verdict"],
        ),
        ("fin-income-3y", "", "pass", []),  # 这一题没有必须提醒的内容
    ],
)
def test_caveats_are_looked_for_in_the_answer(case_id, text, level, missed):
    found, reasons = judge_caveats(BY_ID[case_id], arm(case_id, answer_text=text))
    assert found == level
    assert [r.split(":")[0].removeprefix("caveat ") for r in reasons] == missed


def test_caveats_cannot_be_judged_without_an_answer():
    case = BY_ID["fin-h1-2026"]
    assert judge_caveats(case, arm(case.case_id))[0] == "undecidable"
    assert judge_caveats(case, arm(case.case_id, answer_text="  "))[0] == "undecidable"
    waiting = arm(case.case_id, answer_text="未核实", state="WAITING")
    assert judge_caveats(case, waiting)[0] == "undecidable"


def test_a_case_without_truth_queries_is_judged_on_its_caveats(duck_store):
    case = BY_ID["fin-no-advice"]
    verdict = judge(
        case, arm(case.case_id, answer_text="本体检不提供买卖建议。"), duck_store
    )
    assert (verdict.quality, verdict.citation, verdict.caveats) == (
        "pass",
        "pass",
        "pass",
    )
    advice = judge(case, arm(case.case_id, answer_text="建议买入。"), duck_store)
    assert advice.quality == "pass" and advice.caveats == "fail"
    assert advice.as_dict()["caveats"] == "fail"
    assert "caveat k-no-advice: 不给出买卖持有或目标价" in advice.reasons


def test_a_case_with_truth_queries_still_needs_sql(duck_store):
    case = BY_ID["fin-income-3y"]
    verdict = judge(case, arm(case.case_id, answer_text="…"), duck_store)
    assert verdict.quality == "undecidable" and verdict.reasons == ["no SQL produced"]


def test_a_dataset_of_another_version_cannot_be_judged(tmp_path):
    other = Case(
        case_id="x",
        category="t",
        question="q",
        data_snapshot_id="sh600009-financials-39a395bfa6f16b67",
        truth_queries=(),
    )
    verdict = judge(other, arm("x", answer_text="a"), TruthStore(DATASET))
    assert verdict.quality == "undecidable" and "!= case" in verdict.reasons[0]


def test_the_report_has_a_column_for_caveats(duck_store):
    case = BY_ID["fin-no-advice"]
    verdicts = [
        judge(case, arm(case.case_id, answer_text="不提供买卖建议"), duck_store),
        judge(case, arm(case.case_id, answer_text="建议买入"), duck_store),
    ]
    summaries = summarize(verdicts)
    assert summaries["pack"].caveats_pass == 1 and summaries["pack"].n == 2
    assert summaries["pack"].as_dict()["caveats_pass"] == 1
    text = render_markdown("t", verdicts, summaries, None, {})
    assert "| 臂 | 案例 | 质量通过 | 引用通过 | 提醒通过 |" in text
    assert "| fin-no-advice | pack | pass | pass | fail |" in text


# ---------------------------------------------------------------- 跑臂的辅助


def event(tool: str, arguments: dict, content: dict | None, turn="t1", task=None):
    return {
        "type": "item/completed",
        "task_id": task,
        "payload": {
            "turnId": turn,
            "item": {
                "type": "mcpToolCall",
                "tool": tool,
                "arguments": arguments,
                "result": {"structuredContent": content} if content else None,
            },
        },
    }


def test_sql_is_taken_from_what_was_really_run():
    events = [
        event(
            "run_sql",
            {"sql": "SELECT 1", "dataset": "d"},
            {"citation": {"data_version": VERSION}},
        ),
        event(
            "query_metric",
            {"metrics": ["gross_margin"], "dataset": "d"},
            {"sql": "SELECT 2", "citation": {"data_version": VERSION}},
        ),
        event("query_metric", {"metrics": ["x"]}, None),  # 被拒绝的调用没有 SQL
        event("metric_definitions", {}, {"citation": {"data_version": VERSION}}),
        event("run_sql", {"sql": "SELECT 3"}, None, turn="t2"),
        {"type": "turn/completed", "payload": {}},
    ]
    sqls, versions = sqls_from_events(events, turn_id="t1")
    assert sqls == ["SELECT 1", "SELECT 2"]
    assert versions == [VERSION, VERSION, VERSION]
    assert sqls_from_events(events)[0] == ["SELECT 1", "SELECT 2", "SELECT 3"]


def test_the_default_baseline_prompt_is_unchanged():
    assert baseline_prompt(None) is BASELINE_PROMPT
    assert "retail operations dataset" in BASELINE_PROMPT
    prompt = baseline_prompt("sh600009-financials")
    assert 'dataset="sh600009-financials"' in prompt and "retail" not in prompt
    assert prompt.endswith("Question: ")


def test_the_text_of_an_answer_is_every_string_in_it():
    note = {
        "answer": "收入回升",
        "caveats": ["2021 年为追溯调整后口径", {"text": "中报未核实"}],
        "tables": [{"rows": [{"fiscal_year": 2025, "note": "原始披露"}]}],
        "conclusion": "",
        "data_version": VERSION,
    }
    text = answer_text(note)
    for part in ("收入回升", "追溯调整后", "中报未核实", "原始披露", VERSION):
        assert part in text
    assert "caveats" not in text and "2025" not in text  # 键名与数字不算
    assert answer_text(None) is None and answer_text({}) is None
    assert answer_text("直接的回答") == "直接的回答"
    assert answer_text(json.loads("[]")) is None
