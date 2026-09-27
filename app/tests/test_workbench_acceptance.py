"""确定性验收的规则（F-ACCEPT-01）：纯函数，不需要数据库。重点是 0008-info 段四新增的三种。"""

from __future__ import annotations

import json

import pytest

from app.application.workbench.acceptance import judge
from app.domain.workbench.packs import AcceptanceRule


def verdict(content, *rules: AcceptanceRule):
    text = content if isinstance(content, str) else json.dumps(content)
    return judge(text, rules)


# ---------------------------------------------------------------- all_equal

BALANCED = AcceptanceRule(
    kind="all_equal", path="checks", field="unbalanced", equals=0, message="勾稽不平"
)


@pytest.mark.parametrize(
    "checks",
    [
        [{"rule_id": "R01", "unbalanced": 0}],
        [{"rule_id": "R01", "unbalanced": 0}, {"rule_id": "R02", "unbalanced": 0.0}],
        [],  # 空列表算通过；要求非空另用 list_min
    ],
)
def test_all_equal_passes_when_every_item_has_the_value(checks):
    assert verdict({"checks": checks}, BALANCED).ok


@pytest.mark.parametrize(
    ("checks", "detail"),
    [
        ([{"unbalanced": 0}, {"unbalanced": 1}], "items [1] differ"),
        ([{"unbalanced": 2}, {"unbalanced": 0}, {"unbalanced": 3}], "[0, 2]"),
        ([{"unbalanced": 0.5}], "items [0] differ"),
        ([{"unbalanced": "0"}], "items [0] differ"),  # 文字不是数字
        ([{"unbalanced": None}], "items [0] differ"),
        ([{"unbalanced": False}], "items [0] differ"),  # False 不是 0
        ([{"rule_id": "R01"}], "items [0] differ"),  # 没有这个字段
        (["R01"], "items [0] differ"),  # 项不是对象
        ({"unbalanced": 0}, "not a list"),
        (None, "not a list"),
        ("none", "not a list"),
    ],
)
def test_all_equal_fails_otherwise(checks, detail):
    result = verdict({"checks": checks}, BALANCED)
    assert not result.ok
    assert result.failures == ["all_equal(checks): 勾稽不平"]
    assert detail in result.checks[0]["detail"]


def test_all_equal_on_a_missing_path_fails():
    assert not verdict({"other": []}, BALANCED).ok


def test_all_equal_compares_text_and_booleans_by_type():
    same_basis = AcceptanceRule(
        kind="all_equal", path="rows", field="basis", equals="原始披露"
    )
    assert verdict({"rows": [{"basis": "原始披露"}]}, same_basis).ok
    assert not verdict({"rows": [{"basis": "追溯调整后"}]}, same_basis).ok
    applicable = AcceptanceRule(
        kind="all_equal", path="rows", field="applicable", equals=True
    )
    assert verdict({"rows": [{"applicable": True}]}, applicable).ok
    assert not verdict({"rows": [{"applicable": 1}]}, applicable).ok  # 1 不是 True


def test_all_equal_without_a_field_never_passes():
    rule = AcceptanceRule(kind="all_equal", path="checks", equals=0)
    assert not verdict({"checks": [{"unbalanced": 0}]}, rule).ok


# ---------------------------------------------------------------- list_empty

NO_MISMATCH = AcceptanceRule(kind="list_empty", path="mismatched", message="对不上")


def test_list_empty_passes_only_on_an_empty_list():
    assert verdict({"mismatched": []}, NO_MISMATCH).ok


@pytest.mark.parametrize(
    "value", [[{"item": "operate_income"}], [None], None, "", 0, {}, "[]", False]
)
def test_list_empty_fails_on_anything_else(value):
    result = verdict({"mismatched": value}, NO_MISMATCH)
    assert not result.ok and result.failures == ["list_empty(mismatched): 对不上"]


def test_list_empty_fails_when_the_key_is_missing():
    # 没交这一栏不等于「没有不一致」
    assert not verdict({"matched": []}, NO_MISMATCH).ok


# ---------------------------------------------------------------- blank

BLANK = AcceptanceRule(kind="blank", path="conclusion", message="结论栏必须留空")


@pytest.mark.parametrize("content", [{"conclusion": ""}, {"conclusion": "  \n"}, {}])
def test_blank_passes_when_there_is_no_content(content):
    assert verdict(content | {"answer": "x"}, BLANK).ok


def test_blank_passes_on_null():
    assert verdict({"conclusion": None}, BLANK).ok


@pytest.mark.parametrize("value", ["经营在恢复", "无", 0, False, [], ["x"], {"a": 1}])
def test_blank_fails_when_anything_was_written(value):
    result = verdict({"conclusion": value}, BLANK)
    assert not result.ok and result.failures == ["blank(conclusion): 结论栏必须留空"]


# ---------------------------------------------------------------- 措辞检查的范围

ADVICE = AcceptanceRule(kind="no_positioning_advice", message="含投资建议措辞")


@pytest.mark.parametrize(
    "note",
    [
        {"answer": "收入增长。", "observations": ["建议买入"]},
        {"answer": "收入增长。", "caveats": [{"text": "目标价 40 元"}]},
        {"answer": "收入增长。", "tables": [{"rows": [{"note": "Strong Buy"}]}]},
        {"answer": "综合来看强烈推荐"},
    ],
)
def test_without_a_path_the_whole_artifact_is_checked(note):
    assert not verdict(note, ADVICE).ok


def test_field_names_and_plain_facts_are_not_advice():
    note = {
        "answer": "2025 年营业收入 100 亿元，同比增加。本体检不提供买卖建议。",
        "observations": ["毛利率由 0.30 升至 0.35"],
        "目标价": None,  # 键名不算
    }
    assert verdict(note, ADVICE).ok


def test_with_a_path_only_that_field_is_checked_as_before():
    rule = AcceptanceRule(kind="no_positioning_advice", path="answer")
    assert verdict({"answer": "事实", "other": "建议买入"}, rule).ok
    assert not verdict({"answer": "建议买入"}, rule).ok
    assert verdict({"answer": ["not", "text"]}, rule).ok is True


# ---------------------------------------------------------------- 原有规则不变


def test_existing_rules_behave_as_before():
    rules = (
        AcceptanceRule(kind="json_object"),
        AcceptanceRule(kind="required_keys", keys=("a", "b")),
        AcceptanceRule(kind="non_empty", path="a"),
        AcceptanceRule(kind="list_min", path="b", min_items=2),
        AcceptanceRule(kind="max_chars", max_chars=200),
    )
    assert judge('```json\n{"a": "x", "b": [1, 2]}\n```', rules).ok
    bad = judge('{"a": " ", "b": [1]}', rules)
    assert [c["pass"] for c in bad.checks] == [True, True, False, False, True]
    assert not judge("no json", rules).ok
    assert not judge("[1, 2]", rules).ok


def test_nested_paths():
    rule = AcceptanceRule(
        kind="all_equal", path="result.checks", field="unbalanced", equals=0
    )
    assert verdict({"result": {"checks": [{"unbalanced": 0}]}}, rule).ok
    assert not verdict({"result": {"checks": [{"unbalanced": 4}]}}, rule).ok
    assert not verdict({"result": []}, rule).ok
