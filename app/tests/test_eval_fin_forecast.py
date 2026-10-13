"""财务分析与预测十题（k8s 仓 tree-build/SDD/modules/0023-fin-forecast.md 第六节）。

数据集是知识服务按 1/20 抽样生成的 Olist 样例（knowledge-backend `scripts/build_olist_dataset.py`）。
真值写的是 DuckDB 方言（知识服务开着语义层，专家写的就是这种），所以只在 DuckDB 上跑。
结果列用口径名作别名，期间列叫 `period`：专家包的方法要求这样写，评测按列名逐字比对。
不访问网络，不需要数据库。
"""

from __future__ import annotations

import sqlite3
from decimal import Decimal
from pathlib import Path

import pytest

from eval.cases import load_cases
from eval.judge import ArmOutput, judge, judge_caveats
from eval.truth import TruthStore

DATASET = Path(__file__).parents[1] / "eval/fixtures/olist-ecommerce.dataset.bin"
VERSION = "olist-ecommerce-dcca129e2ea0cb56"
CASES = load_cases("fin_forecast_10")
BY_ID = {c.case_id: c for c in CASES}
# 口径表里的口径名，加上期间与预测的规范列名；其余必须是数据集里真有的字段
CANONICAL = {
    "gmv",
    "item_revenue",
    "freight_revenue",
    "payment_total",
    "order_count",
    "cancel_rate",
    "credit_card_installment_share",
    "active_customers",
    "repeat_customer_share",
    "period",
    "forecast_point",
    "forecast_low",
    "forecast_high",
}


@pytest.fixture(scope="module")
def store() -> TruthStore:
    s = TruthStore(DATASET, dialect="duckdb")
    yield s
    s.close()


def arm(case_id: str, **changes) -> ArmOutput:
    base = {"data_versions": [VERSION], "cost": Decimal("0.1")}
    return ArmOutput(case_id, "pack", **(base | changes))


# ---------------------------------------------------------------- 案例集


def test_ten_cases_frozen_on_one_data_version(store):
    assert len(CASES) == 10 and len(BY_ID) == 10
    assert {c.data_snapshot_id for c in CASES} == {VERSION}
    assert store.data_version() == VERSION
    assert sum(len(c.truth_queries) for c in CASES) == 11
    assert [c.case_id for c in CASES if not c.truth_queries] == ["ff-category-margin"]
    ids = [q.query_id for c in CASES for q in c.truth_queries]
    assert len(set(ids)) == len(ids)
    # 两道预测题、一道只考边界的题都有必须提醒
    assert {c.case_id for c in CASES if c.caveat_checks} == {
        "ff-gmv-quarterly-yoy",
        "ff-category-margin",
        "ff-demo-forecast",
        "ff-backtest-orders",
    }


def test_truth_columns_are_metric_names_or_dataset_fields():
    with sqlite3.connect(f"file:{DATASET}?mode=ro", uri=True) as db:
        tables = [r[0] for r in db.execute("SELECT name FROM sqlite_master")]
        fields = {
            row[1] for t in tables for row in db.execute(f'PRAGMA table_info("{t}")')
        }
        metrics = {
            r[0] for r in db.execute("SELECT metric_name FROM metric_dictionary")
        }
    assert CANONICAL - {
        "period",
        "forecast_point",
        "forecast_low",
        "forecast_high",
    } <= (metrics)
    for case in CASES:
        for q in case.truth_queries:
            unknown = set(q.expected_columns) - CANONICAL - fields
            assert not unknown, (q.query_id, unknown)


def test_every_truth_query_runs_and_gives_its_columns(store):
    for case in CASES:
        for q in case.truth_queries:
            columns, rows = store.truth(q)
            assert set(q.expected_columns) <= set(columns), q.query_id
            assert rows, q.query_id


def test_truth_values_are_the_ones_checked_by_hand(store):
    """手算的数（直接读 parquet、或在 SQLite 上按 0023 的口径算）与真值查询一致。"""

    def rows(query_id: str) -> list[dict]:
        q = next(q for c in CASES for q in c.truth_queries if q.query_id == query_id)
        return store.truth(q)[1]

    monthly = rows("q-gmv-monthly")
    assert len(monthly) == 20
    assert monthly[0] == {
        "period": "2017-01",
        "gmv": pytest.approx(5811.13),
        "order_count": 33,
    }
    assert monthly[-1] == {
        "period": "2018-08",
        "gmv": pytest.approx(44372.02),
        "order_count": 332,
    }
    quarters = {r["period"]: r["gmv"] for r in rows("q-gmv-quarterly")}
    assert quarters["2018-Q2"] / quarters["2017-Q2"] - 1 == pytest.approx(
        1.5505, abs=1e-4
    )
    assert rows("q-installment-share")[0][
        "credit_card_installment_share"
    ] == pytest.approx(0.66859, abs=1e-5)
    assert rows("q-repeat-share")[0]["repeat_customer_share"] == pytest.approx(
        0.032652, abs=1e-6
    )
    assert None not in {
        r["product_category_name_english"] for r in rows("q-category-top10")
    }
    demo = {r["period"]: r for r in rows("q-demo-quarters")}
    assert demo["2018-Q3"]["forecast_point"] == pytest.approx(151098.28)
    assert (demo["2018-Q3"]["forecast_low"], demo["2018-Q3"]["forecast_high"]) == (
        pytest.approx(138205.61),
        pytest.approx(163540.61),
    )
    assert demo["2018-Q4"]["forecast_point"] == pytest.approx(171794.07)
    assert (demo["2018-Q4"]["forecast_low"], demo["2018-Q4"]["forecast_high"]) == (
        pytest.approx(133116.06),
        pytest.approx(209121.06),
    )
    assert rows("q-backtest-forecast") == [
        {"forecast_point": 286.5, "forecast_low": 186, "forecast_high": 391}
    ]
    assert [r["order_count"] for r in rows("q-backtest-actual")] == [
        328,
        339,
        368,
        354,
        310,
        332,
    ]


# ---------------------------------------------------------------- 判定


def test_the_right_sql_with_the_right_names_passes(store):
    case = BY_ID["ff-installment-share"]
    sql = case.truth_queries[0].sql
    verdict = judge(case, arm(case.case_id, sqls=[sql], answer_text="约 66.9%"), store)
    assert (verdict.quality, verdict.citation, verdict.caveats) == (
        "pass",
        "pass",
        "pass",
    )


def test_the_same_number_under_another_column_name_does_not_pass(store):
    case = BY_ID["ff-installment-share"]
    sql = case.truth_queries[0].sql.replace(
        "credit_card_installment_share", "share_of_installments"
    )
    verdict = judge(case, arm(case.case_id, sqls=[sql]), store)
    assert verdict.quality == "fail"
    assert "columns missing: credit_card_installment_share" in verdict.reasons[0]


def test_counting_repeat_customers_by_order_customer_id_fails(store):
    case = BY_ID["ff-repeat-customers"]
    wrong = (
        "SELECT AVG(CASE WHEN n >= 2 THEN 1.0 ELSE 0.0 END) AS repeat_customer_share "
        "FROM (SELECT customer_id, COUNT(*) AS n FROM orders GROUP BY 1)"
    )
    assert store.query(wrong)[1] == [{"repeat_customer_share": 0.0}]
    verdict = judge(case, arm(case.case_id, sqls=[wrong]), store)
    assert verdict.quality == "fail"


def test_revenue_with_canceled_orders_does_not_pass(store):
    case = BY_ID["ff-gmv-monthly"]
    wrong = case.truth_queries[0].sql.replace(
        "WHERE o.order_status NOT IN ('canceled', 'unavailable') AND", "WHERE"
    )
    assert wrong != case.truth_queries[0].sql
    assert judge(case, arm(case.case_id, sqls=[wrong]), store).quality == "fail"
    right = judge(case, arm(case.case_id, sqls=[case.truth_queries[0].sql]), store)
    assert right.quality == "pass"


@pytest.mark.parametrize(
    ("text", "level", "missed"),
    [
        ("数据集不含成本，毛利率算不出来；可以给出各品类的商品收入。", "pass", []),
        ("用运费占比近似，毛利率为 18%。", "fail", ["k-no-cost", "k-no-fake-margin"]),
        ("没有成本数据。但按售价估计，毛利率为 30%。", "fail", ["k-no-fake-margin"]),
    ],
)
def test_the_margin_question_needs_an_honest_no(text, level, missed):
    case = BY_ID["ff-category-margin"]
    found, reasons = judge_caveats(case, arm(case.case_id, answer_text=text))
    assert found == level
    assert [r.split(":")[0].removeprefix("caveat ") for r in reasons] == missed


def test_the_demo_forecast_needs_both_queries_and_its_caveats(store):
    case = BY_ID["ff-demo-forecast"]
    sqls = [q.sql for q in case.truth_queries]
    good = "2018-09 起数据截断，不当成下滑。假设近期水平延续。三季度约 15.1 万，四季度约 17.2 万。"
    verdict = judge(case, arm(case.case_id, sqls=sqls, answer_text=good), store)
    assert (verdict.quality, verdict.citation, verdict.caveats) == (
        "pass",
        "pass",
        "pass",
    )
    only_trend = judge(case, arm(case.case_id, sqls=sqls[:1], answer_text=good), store)
    assert (
        only_trend.quality == "fail" and only_trend.coverage["q-demo-quarters"] is None
    )
    promise = judge(
        case, arm(case.case_id, sqls=sqls, answer_text="9 月以后一定会回升。"), store
    )
    assert promise.quality == "pass" and promise.caveats == "fail"
    assert {r.split(":")[0] for r in promise.reasons} == {
        "caveat k-truncation",
        "caveat k-assumption",
        "caveat k-no-promise",
    }


# ---------------------------------------------------------------- 装载


@pytest.mark.parametrize(
    "dataset",
    [
        DATASET,
        Path(__file__).parents[1] / "eval/fixtures/sh600009-financials-v2.dataset.bin",
    ],
)
def test_the_duckdb_copy_is_the_sqlite_file_row_for_row(dataset):
    duck = TruthStore(dataset, dialect="duckdb")
    try:
        db = duck._duckdb()
        with sqlite3.connect(f"file:{dataset}?mode=ro", uri=True) as src:
            tables = [
                r[0]
                for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%'"
                )
            ]
            for table in tables:
                left = sorted(
                    src.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr
                )
                right = sorted(
                    db.execute(f'SELECT * FROM "{table}"').fetchall(), key=repr
                )
                assert left == right, table
    finally:
        duck.close()


def test_awkward_values_survive_the_copy(tmp_path):
    path = tmp_path / "awkward.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE dataset_metadata (key TEXT, value TEXT)")
        db.execute("INSERT INTO dataset_metadata VALUES ('data_snapshot_id', 'v1')")
        db.execute("CREATE TABLE t (n INTEGER, x REAL, s TEXT)")
        db.executemany(
            "INSERT INTO t VALUES (?, ?, ?)",
            [
                (1, 0.1 + 0.2, ""),
                (2, None, None),
                (None, 1e-12, 'say "hi", then\nnext line'),
                (4, 123456789.987654321, "逗号,与'单引号'"),
            ],
        )
    duck = TruthStore(path, dialect="duckdb")
    try:
        got = duck.query("SELECT n, x, s FROM t ORDER BY n NULLS LAST")[1]
    finally:
        duck.close()
    assert got == [
        {"n": 1, "x": 0.1 + 0.2, "s": ""},
        {"n": 2, "x": None, "s": None},
        {"n": 4, "x": 123456789.987654321, "s": "逗号,与'单引号'"},
        {"n": None, "x": 1e-12, "s": 'say "hi", then\nnext line'},
    ]
