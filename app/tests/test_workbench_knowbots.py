"""分身清单变成专家包（SDD 0027 P4）：只认运行方是这里的、工具与审批不比平台松、不用数据线工具。"""

from __future__ import annotations

import hashlib
import json

import pytest
from knowbot_fixtures import KNOWBOT, manifest

from app.application.workbench.catalog import (
    SOURCE_KNOWMART,
    expert_name,
    pack_for_task,
)
from app.domain.workbench import knowbots
from app.domain.workbench.errors import KnowbotInvalid


def _pack(doc, version="1"):
    return knowbots.pack_from_manifest(doc, knowbot_id=KNOWBOT, version=version)


def test_a_good_manifest_becomes_the_owners_expert_pack() -> None:
    pack = _pack(manifest())
    assert pack.profile_id == f"knowbot:{KNOWBOT}@1"
    assert pack.name == "我的方法" and pack.tagline == "按我的口径看一家公司"
    assert pack.internal is False and pack.status == "ready"
    assert pack.version == "1" and pack.workflow


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"runtime": "elsewhere"}, "runtime must be investment"),
        ({"schema": "x"}, "schema must be"),
        ({"version": "2"}, "version differs"),
        ({"pack": None}, "pack must be an object"),
    ],
)
def test_the_outer_layer_must_be_for_this_runtime_and_version(change, says) -> None:
    with pytest.raises(KnowbotInvalid, match=says):
        _pack(manifest() | change)


def test_a_pack_that_is_not_an_expert_pack_is_refused_without_echoing_it() -> None:
    doc = manifest()
    del doc["pack"]["workflow"]
    with pytest.raises(KnowbotInvalid) as raised:
        _pack(doc)
    assert "workflow" in raised.value.message


@pytest.mark.parametrize(
    "pack_change",
    [
        {"tool_policy": {"command": "allow"}},
        {"tool_policy": {"network": "allow"}},
        {"tool_policy": {"file_change_outside": "ask"}},
        {"tool_policy": {"max_questions": 9}},
        {"tool_policy": {"command_allow": [["rm", "-rf"]]}},
        {"requires_tools": True},
    ],
)
def test_a_knowbot_is_never_looser_than_the_platform(pack_change) -> None:
    with pytest.raises(KnowbotInvalid):
        _pack(manifest(**pack_change))


def test_a_knowbot_step_cannot_use_data_line_tools_or_allow_commands() -> None:
    doc = manifest()
    doc["pack"]["workflow"][0]["tools"] = ["run_sql"]  # 数据线的工具（C-L2）
    with pytest.raises(KnowbotInvalid, match="tools a knowbot cannot use"):
        _pack(doc)
    doc = manifest()
    doc["pack"]["workflow"][0]["tool_policy"] = {"command_allow": [["ls"]]}
    with pytest.raises(KnowbotInvalid, match="allows commands by prefix"):
        _pack(doc)


def test_stricter_policies_are_fine() -> None:
    pack = _pack(
        manifest(tool_policy={"command": "deny", "file_change_in_project": "ask"})
    )
    assert pack.tool_policy.command == "deny"


def test_refs_and_profile_ids() -> None:
    assert knowbots.parse_ref(f"{KNOWBOT}@3") == (KNOWBOT, "3")
    for bad in (
        None,
        "",
        KNOWBOT,
        f"{KNOWBOT}@0",
        f"{KNOWBOT}@1@2",
        "x@1",
        f"{KNOWBOT.upper()}@1",
    ):
        assert knowbots.parse_ref(bad) is None
    pid = knowbots.profile_id(KNOWBOT, "3")
    assert knowbots.parse_profile(pid) == (KNOWBOT, "3")
    assert knowbots.is_knowbot(pid) and not knowbots.is_knowbot("FIN_REVIEW")


def test_the_digest_is_knowmarts_canonical_json_sha256() -> None:
    doc = manifest()
    body = json.dumps(doc, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    want = "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest()
    assert knowbots.manifest_digest(doc) == want


def test_tasks_read_their_own_snapshot() -> None:
    pack = _pack(manifest())
    task = {
        "profile_id": pack.profile_id,
        "profile_version": "1",
        "pack_source": SOURCE_KNOWMART,
        "pack_digest": "sha256:" + "a" * 64,
        "pack_snapshot": pack.model_dump(mode="json"),
    }
    first = pack_for_task(task)
    assert first == pack
    assert pack_for_task(dict(task)) is first  # 同一份快照只解析一次
    assert pack_for_task(task | {"pack_snapshot": None}) is None
    builtin = pack_for_task({"profile_id": "FIN_REVIEW", "profile_version": None})
    assert builtin is not None and builtin.profile_id == "FIN_REVIEW"
    assert expert_name(task) == "我的方法"
    assert (
        expert_name({"pack_source": SOURCE_KNOWMART, "pack_name": "列表行"}) == "列表行"
    )
