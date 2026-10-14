"""成稿文件有没有写出来，只认这一轮 Codex 报的 fileChange 路径（0015 §3.3，账 80）。"""

from __future__ import annotations

from app.domain.workbench.deliverables import (
    DELIVERABLE_RULES,
    HANDBACK_RULES,
    WRITE_FILE_RULES,
    changed_paths,
    deliverable_seen,
)


def test_matches_by_path_tail_across_windows_and_posix():
    paths = [
        "C:\\Users\\u\\研究\\恒瑞\\底稿\\财报体检-成稿.md",
        "/home/u/research/hengrui/交回.md",
    ]
    assert deliverable_seen(paths, "底稿/财报体检-成稿.md")
    assert deliverable_seen(paths, "交回.md")
    assert not deliverable_seen(paths, "底稿/交回.md")
    assert not deliverable_seen(paths, "成稿.md")
    assert not deliverable_seen(paths, "")
    assert not deliverable_seen([], "交回.md")


def test_changed_paths_only_from_file_change_items():
    item = {
        "type": "fileChange",
        "changes": [{"path": "/p/a.md", "kind": "add"}, {"kind": "x"}],
    }
    assert changed_paths(item) == ["/p/a.md"]
    assert (
        changed_paths({"type": "commandExecution", "changes": [{"path": "/p/b"}]}) == []
    )
    assert changed_paths(None) == []


def test_the_rules_forbid_ratings_and_raw_json_and_name_the_handback_file():
    assert "buy/sell" in DELIVERABLE_RULES and "raw JSON" in DELIVERABLE_RULES
    assert "交回.md" in HANDBACK_RULES and "怎么恢复" in HANDBACK_RULES


def test_files_are_written_whole_so_an_earlier_version_never_blocks_them():
    # 2026-10-14 现网：Windows 上改昨天那份成稿，补丁对不上、bash 语法交给 PowerShell，一步耗满 15 分钟
    assert "*** Add File:" in WRITE_FILE_RULES and "whole content" in WRITE_FILE_RULES
    assert "never use `*** Update File`" in WRITE_FILE_RULES
    assert "Never write them through the shell" in WRITE_FILE_RULES
    assert "PowerShell" in WRITE_FILE_RULES
    assert "Add File" in HANDBACK_RULES
