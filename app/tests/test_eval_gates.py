"""评测规则（0007）：真值比对、单案判定只有三种结论、发布门按质量/引用/费用判（AT-27、F-EVAL-05）。"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from eval.cases import Case, TruthQuery, load_cases
from eval.judge import ArmOutput, judge
from eval.report import ArmSummary, release_gate, render_markdown, summarize
from eval.truth import TruthStore, fingerprint, same_result


@pytest.fixture
def db(tmp_path: Path) -> Path:
    path = tmp_path / "mini.sqlite"
    with sqlite3.connect(path) as c:
        c.executescript(
            """
            CREATE TABLE dataset_metadata(key TEXT, value TEXT);
            INSERT INTO dataset_metadata VALUES ('data_snapshot_id','mini-v1');
            CREATE TABLE order_performance(order_year INTEGER, net_revenue_cents INTEGER);
            INSERT INTO order_performance VALUES (2024, 100),(2024, 200),(2025, 400);
            """
        )
    return path


TQ = TruthQuery(
    query_id="q1",
    sql="SELECT order_year, SUM(net_revenue_cents) AS net_revenue_cents FROM order_performance GROUP BY order_year",
    expected_columns=("order_year", "net_revenue_cents"),
    key_columns=("order_year",),
    value_columns=("net_revenue_cents",),
    tolerance=0.5,
)
CASE = Case(
    case_id="c1",
    category="t",
    question="q",
    data_snapshot_id="mini-v1",
    truth_queries=(TQ,),
)


def test_frozen_case_set_loads_twenty_cases_on_one_data_version():
    cases = load_cases()
    assert len(cases) == 20
    assert {c.data_snapshot_id for c in cases} == {"lesson23-analysis-b7ad59fddab30331"}
    assert all(c.truth_queries for c in cases)


def test_same_result_ignores_row_order_and_extra_columns_within_tolerance(db):
    store = TruthStore(db)
    tcols, trows = store.truth(TQ)
    cand_cols = ["net_revenue_cents", "order_year", "extra"]
    cand_rows = [
        {"order_year": 2025, "net_revenue_cents": 400.4, "extra": 1},
        {"order_year": 2024, "net_revenue_cents": 300, "extra": 2},
    ]
    ok, why = same_result(cand_cols, cand_rows, tcols, trows, TQ)
    assert ok, why
    exact = [
        {"order_year": 2025, "net_revenue_cents": 400},
        {"order_year": 2024, "net_revenue_cents": 300},
    ]
    assert fingerprint(exact, TQ) == fingerprint(trows, TQ)
    assert fingerprint(cand_rows, TQ) != fingerprint(
        trows, TQ
    )  # 指纹是精确的，容差只在比对里


@pytest.mark.parametrize(
    "rows, why",
    [
        ([{"order_year": 2024, "net_revenue_cents": 300}], "row count"),
        (
            [
                {"order_year": 2024, "net_revenue_cents": 301},
                {"order_year": 2025, "net_revenue_cents": 400},
            ],
            "column net_revenue_cents",
        ),
    ],
)
def test_same_result_reports_reason(db, rows, why):
    store = TruthStore(db)
    tcols, trows = store.truth(TQ)
    ok, reason = same_result(
        ["order_year", "net_revenue_cents"], rows, tcols, trows, TQ
    )
    assert not ok and why in reason


def test_missing_expected_column_fails_not_undecidable(db):
    store = TruthStore(db)
    out = ArmOutput(
        case_id="c1",
        arm="x",
        sqls=[
            "SELECT order_year, SUM(net_revenue_cents) AS net FROM order_performance GROUP BY order_year"
        ],
        data_versions=["mini-v1"],
    )
    v = judge(CASE, out, store)
    assert v.quality == "fail" and v.citation == "pass"
    assert "columns missing: net_revenue_cents" in v.reasons[0]


def test_judge_pass_uses_any_of_the_candidate_sqls(db):
    store = TruthStore(db)
    out = ArmOutput(
        case_id="c1",
        arm="pack",
        sqls=["SELECT 1 AS x", TQ.sql],
        data_versions=["mini-v1", "mini-v1"],
        cost=Decimal("0.5"),
    )
    v = judge(CASE, out, store)
    assert v.quality == "pass" and v.citation == "pass" and v.coverage == {"q1": 1}


def test_judge_undecidable_when_arm_did_not_complete_or_version_differs(db):
    store = TruthStore(db)
    v = judge(CASE, ArmOutput("c1", "pack", state="WAITING", error="budget"), store)
    assert (v.quality, v.citation) == ("undecidable", "undecidable")
    other = Case("c2", "t", "q", "mini-v2", (TQ,))
    v = judge(
        other, ArmOutput("c2", "pack", sqls=[TQ.sql], data_versions=["mini-v1"]), store
    )
    assert v.quality == "undecidable" and "version" in v.reasons[0]


def test_judge_fails_citation_when_version_not_cited(db):
    store = TruthStore(db)
    v = judge(CASE, ArmOutput("c1", "base", sqls=[TQ.sql]), store)
    assert v.quality == "pass" and v.citation == "fail"


def test_broken_candidate_sql_is_a_fail_with_reason(db):
    store = TruthStore(db)
    v = judge(
        CASE,
        ArmOutput("c1", "base", sqls=["SELECT * FROM nope"], data_versions=["mini-v1"]),
        store,
    )
    assert v.quality == "fail" and any("sql#0 failed" in r for r in v.reasons)


def _summary(arm: str, n: int, q: int, und: int, cost: str) -> ArmSummary:
    return ArmSummary(arm, n, q, q, und, Decimal(cost))


def test_release_gate_rules():
    assert (
        release_gate(
            _summary("b", 20, 10, 0, "10"), _summary("p", 20, 16, 0, "40")
        ).level
        == "pass"
    )
    g = release_gate(_summary("b", 20, 10, 0, "10"), _summary("p", 20, 11, 0, "40"))
    assert g.level == "fail" and "dearer" in g.reason
    g = release_gate(_summary("b", 20, 10, 0, "10"), _summary("p", 20, 11, 0, "10"))
    assert g.level == "pass"  # 不更贵，小幅提升也算
    assert (
        release_gate(
            _summary("b", 20, 10, 0, "10"), _summary("p", 20, 10, 0, "5")
        ).level
        == "fail"
    )
    g = release_gate(_summary("b", 20, 10, 0, "10"), _summary("p", 20, 18, 3, "10"))
    assert g.level == "undecidable" and "undecidable" in g.reason
    assert (
        release_gate(_summary("b", 0, 0, 0, "0"), _summary("p", 20, 18, 0, "10")).level
        == "undecidable"
    )


def test_summary_and_markdown(db):
    store = TruthStore(db)
    verdicts = [
        judge(
            CASE,
            ArmOutput(
                "c1",
                "baseline",
                sqls=[TQ.sql],
                data_versions=["mini-v1"],
                cost=Decimal("0.1"),
            ),
            store,
        ),
        judge(
            CASE, ArmOutput("c1", "pack", state="FAILED", cost=Decimal("0.2")), store
        ),
    ]
    s = summarize(verdicts)
    assert s["baseline"].quality_pass == 1 and s["pack"].undecidable == 1
    md = render_markdown(
        "t", verdicts, s, release_gate(s["baseline"], s["pack"]), {"dataset": "mini-v1"}
    )
    assert "undecidable" in md and "| c1 | pack |" in md
