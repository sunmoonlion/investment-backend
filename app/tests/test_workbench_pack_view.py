"""专家包给用户看的那一面（PRD/apps/investment-expert.md 第四、五、十节，AT-INV-17 至 19）。"""

from __future__ import annotations

import json

import pytest

from app.domain.workbench.pack_view import after_rejection, pack_view
from app.domain.workbench.packs import (
    BUILTIN_PACKS,
    DATA_QUERY,
    FIN_REVIEW,
    SMOKE,
    listed_packs,
)
from app.domain.workbench.step_view import budget_view, money

LISTED = [p.profile_id for p in listed_packs()]


def test_the_smoke_pack_is_ours_and_not_listed():
    assert SMOKE.internal
    assert LISTED == ["DATA_QUERY", "FIN_REVIEW"]


@pytest.mark.parametrize("pack_id", LISTED)
def test_every_listed_pack_can_be_explained_to_a_user(pack_id):
    pack = BUILTIN_PACKS[pack_id]
    assert pack.name and pack.tagline
    for question in (
        "解决什么",
        "不解决什么",
        "输入",
        "输出",
        "谁做判断",
        "失败怎么办",
    ):
        assert pack.answers.get(question), question
    for step in pack.workflow:
        assert step.summary, step.step_id
        for rule in step.acceptance:
            assert rule.label, (step.step_id, rule.kind)
            assert rule.message, (step.step_id, rule.kind)


@pytest.mark.parametrize("pack_id", list(BUILTIN_PACKS))
def test_the_method_never_leaves(pack_id):
    """AT-INV-19"""
    pack = BUILTIN_PACKS[pack_id]
    shown = json.dumps(pack_view(pack), ensure_ascii=False)
    for step in pack.workflow:
        assert step.method_text not in shown
        # 方法里成句的话也不在：取每个方法开头的一段
        assert step.method_text[:60] not in shown
    assert "method" not in shown
    assert "output_schema" not in shown


@pytest.mark.parametrize("pack_id", LISTED)
def test_words_shown_to_users_carry_no_code_names(pack_id):
    """AT-INV-17：任何地方都没有代号。编号只在 id 里，页面用它发请求，不显示。"""
    view = pack_view(BUILTIN_PACKS[pack_id])
    words = [
        view[k]
        for k in ("name", "tagline", "solves", "does_not_solve", "input", "output")
    ]
    for step in view["steps"]:
        words += [step["title"], step["summary"], step["after_rejection"]["text"]]
        words += [c["label"] for c in step["checks"]]
    text = "\n".join(words)
    for code in ("FIN_REVIEW", "DATA_QUERY", "SMOKE", "F-POS", "json", "JSON"):
        assert code not in text, code


def test_the_view_of_the_financial_review():
    view = pack_view(FIN_REVIEW)
    assert view["id"] == "FIN_REVIEW"
    assert view["name"] == "财报体检"
    assert view["uses_our_data"] is True
    assert [s["title"] for s in view["steps"]] == [
        "定范围",
        "数据摸底",
        "勾稽",
        "取数",
        "算指标",
        "对照官方",
        "成稿",
    ]
    assert [s["index"] for s in view["steps"]] == [1, 2, 3, 4, 5, 6, 7]
    reconcile = view["steps"][2]
    assert reconcile["uses"] == [
        {"artifact": "scope", "step": 1, "title": "定范围"},
        {"artifact": "profile", "step": 2, "title": "数据摸底"},
    ]
    assert [c["label"] for c in reconcile["checks"]] == [
        "交回的格式对",
        "有勾稽结果、跨期连续性、未解释的断点",
        "做了勾稽",
        "每条勾稽规则都平",
        "跨期的断点都有解释",
    ]
    assert view["least_budget"] == "0.35"


def test_what_happens_after_a_rejection():
    """三种说法：重做、退回、停下来问你。说的是专家包里写的，不多不少。"""
    said = {
        s.step_id: after_rejection(s, FIN_REVIEW)["text"] for s in FIN_REVIEW.workflow
    }
    assert said == {
        "scope": "重做，最多 1 次；仍不过，停下来问你",
        "profile": "重做，最多 1 次；仍不过，停下来问你",
        "reconcile": "停下来问你",  # 勾稽不平不重做：重做等于催它把数改平
        "extract": "重做，最多 1 次；仍不过，退回第 2 步",
        "metrics": "重做，最多 2 次；仍不过，停下来问你",
        "crosscheck": "停下来问你",
        "note": "重做，最多 1 次；仍不过，停下来问你",
    }
    execute = next(s for s in DATA_QUERY.workflow if s.step_id == "sql_execute")
    assert after_rejection(execute, DATA_QUERY) == {
        "reworks": 1,
        "then": "back",
        "back_to": 2,
        "text": "重做，最多 1 次；仍不过，退回第 2 步",
    }


@pytest.mark.parametrize("pack_id", LISTED)
def test_the_dossier_layout_points_at_things_the_steps_return(pack_id):
    pack = BUILTIN_PACKS[pack_id]
    returned = {s.output_artifact: s for s in pack.workflow}
    assert pack.dossier
    keys = [b.key for b in pack.dossier]
    assert len(keys) == len(set(keys))
    for block in pack.dossier:
        assert block.artifact in returned, block.key
        shape = returned[block.artifact].output_schema
        for path in [block.path, *block.also.values()]:
            if path:
                assert path in shape, (block.key, path)
        if block.columns:
            # 登记的列，是这一步说好要交回的那张表里有的
            (row,) = shape[block.path][:1]
            for key, title in block.columns:
                assert key in row and title, (block.key, key)
    sections = [b.section for b in pack.dossier]
    assert sections[0] == "answer"
    for needed in ("data", "verified", "limits", "sources"):
        assert needed in sections


@pytest.mark.parametrize(
    ("stored", "shown"),
    [
        ("0.030000", "0.03"),
        ("2", "2.00"),
        ("1.021960", "1.02196"),
        ("0.5", "0.50"),
        ("0", "0.00"),
        ({"amount": "0.010000"}, "0.01"),
        ({}, "0.00"),
        (None, "0.00"),
        ("1E+1", "10.00"),
    ],
)
def test_how_money_is_written(stored, shown):
    assert money(stored) == shown


def test_the_budget_as_shown():
    assert budget_view(
        {"currency": "CNY", "limit": "2", "used": "0.180000", "reserved": "0.050000"}
    ) == {
        "currency": "CNY",
        "limit": "2.00",
        "used": "0.18",
        "reserved": "0.05",
        "left": "1.77",
    }


def test_data_that_changed_version_midway_is_not_hidden():
    from app.application.workbench.expert_desk import data_seen

    assert data_seen([{"data": None}, {}]) is None
    assert data_seen(
        [
            {"data": {"dataset": "d", "data_version": "v1", "as_of": "2025-12-31"}},
            {"data": None},
            {"data": {"data_version": "v2"}},
        ]
    ) == {
        "dataset": "d",
        "data_version": "v2",
        "as_of": "2025-12-31",
        "versions_seen": ["v1", "v2"],
    }
