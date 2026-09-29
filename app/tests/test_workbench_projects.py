"""工作区、项目、对话的种类：领域规则（PRD/apps/investment.md）。不碰数据库。"""

from __future__ import annotations

import pytest

from app.domain.workbench.errors import ProjectPathInvalid
from app.domain.workbench.projects import (
    ConversationKind,
    approval_policy,
    clean_relative_path,
    clean_title,
    default_project_title,
    default_title,
    is_windows_root,
    mode_note,
    mode_of,
    project_dir,
    split_under_roots,
    thread_settings,
    turn_settings,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, ""),
        ("", ""),
        (".", ""),
        ("  hengrui  ", "hengrui"),
        ("hengrui/", "hengrui"),
        ("2025/恒瑞医药 年报", "2025/恒瑞医药 年报"),
        ("a/b/c", "a/b/c"),
        ("v1.2/notes", "v1.2/notes"),
    ],
)
def test_relative_paths_that_are_accepted(raw, expected):
    assert clean_relative_path(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "../elsewhere",  # AT-INV-05
        "a/../b",
        "a/./b",
        "a//b",
        "/etc",
        "\\\\server\\share",
        "~/x",
        "C:/Users/me",
        "C:\\Users\\me",
        "a\\b",
        "a:b",
        "what?",
        "pipe|name",
        "nul",
        "COM1.txt",
        "trailing./x",
        "a/ lead/x",
        "x/ trail ",
        "tab\tname",
        "zero\x00",
        "a" * 121,
        "/".join(["seg"] * 101),
        123,
    ],
)
def test_relative_paths_that_are_refused(raw):
    with pytest.raises(ProjectPathInvalid):
        clean_relative_path(raw)


@pytest.mark.parametrize(
    ("root", "windows"),
    [
        ("/home/me/research", False),
        ("/", False),
        ("C:\\Users\\me\\research", True),
        ("c:/Users/me", True),
        ("\\\\nas\\share", True),
    ],
)
def test_the_kind_of_root_is_recognised(root, windows):
    assert is_windows_root(root) is windows


@pytest.mark.parametrize(
    ("root", "relative", "expected"),
    [
        ("/home/me/research", "", "/home/me/research"),
        ("/home/me/research", "hengrui", "/home/me/research/hengrui"),
        ("/home/me/research/", "a/b", "/home/me/research/a/b"),
        ("C:\\Users\\me\\research", "a/b", "C:\\Users\\me\\research\\a\\b"),
        ("C:\\Users\\me\\research\\", "a", "C:\\Users\\me\\research\\a"),
        ("C:/Users/me", "a/b", "C:/Users/me\\a\\b"),
    ],
)
def test_the_directory_follows_the_style_of_the_workspace(root, relative, expected):
    assert project_dir(root, relative) == expected


@pytest.mark.parametrize(
    ("directory", "roots", "expected"),
    [
        ("/home/me/research/a", ["/home/me/research"], ("/home/me/research", "a")),
        ("/home/me/research", ["/home/me/research/"], ("/home/me/research/", "")),
        (
            "/home/me/research/a/b",
            ["/home/me", "/home/me/research"],
            ("/home/me/research", "a/b"),
        ),
        ("/home/me/researcher", ["/home/me/research"], None),
        ("/other", ["/home/me/research"], None),
        ("/anything", [], None),
        (
            "C:\\Users\\Me\\Research\\A\\b",
            ["c:\\users\\me\\research"],
            ("c:\\users\\me\\research", "A/b"),
        ),
    ],
)
def test_finding_the_workspace_of_an_existing_directory(directory, roots, expected):
    assert split_under_roots(directory, roots) == expected


def test_titles():
    assert clean_title(None) is None and clean_title("   ") is None
    assert clean_title("  恒瑞  医药\n研究 ") == "恒瑞 医药 研究"
    assert len(clean_title("字" * 300)) == 200
    assert default_title("毛利率是什么") == "毛利率是什么"
    long = "恒瑞医药 2023 到 2025 年的盈利能力怎么样，和同行比呢，请给出数据与出处"
    assert default_title(long) == long[:30].rstrip() + "…"
    assert default_title("第一行\n第二行") == "第一行 第二行"
    assert default_project_title("a/恒瑞医药", "/home/me") == "恒瑞医药"
    assert default_project_title("", "/home/me/research/") == "research"
    assert default_project_title("", "C:\\Users\\me\\research") == "research"


READ_ONLY = {"type": "readOnly", "networkAccess": False}


def test_chat_outside_a_project_has_no_environment_and_is_read_only():
    settings = turn_settings(
        "chat",
        directory=None,
        environment_key=None,
        environment_online=False,
        approval="on-request",
    )
    assert settings == {
        "environments": [],
        "sandboxPolicy": READ_ONLY,
        "approvalPolicy": "never",
    }


def test_chat_in_a_project_reads_files_only_while_the_machine_is_online():
    common = {
        "directory": "/home/me/research/a",
        "environment_key": "user-pc",
        "approval": "on-request",
    }
    online = turn_settings(ConversationKind.CHAT, environment_online=True, **common)
    assert online == {
        "environments": [{"environmentId": "user-pc", "cwd": "/home/me/research/a"}],
        "cwd": "/home/me/research/a",
        "sandboxPolicy": READ_ONLY,
        "approvalPolicy": "never",
    }
    offline = turn_settings(ConversationKind.CHAT, environment_online=False, **common)
    assert offline["environments"] == [] and "cwd" not in offline
    assert offline["sandboxPolicy"] == READ_ONLY


@pytest.mark.parametrize("policy", ["untrusted", "on-request", "never"])
def test_work_writes_inside_the_project_with_the_users_approval_policy(policy):
    settings = turn_settings(
        "work",
        directory="/home/me/research/a",
        environment_key="user-pc",
        environment_online=True,
        approval=policy,
    )
    assert settings["sandboxPolicy"]["type"] == "workspaceWrite"
    assert settings["sandboxPolicy"]["networkAccess"] is False
    assert settings["approvalPolicy"] == policy
    assert settings["cwd"] == "/home/me/research/a"
    assert settings["environments"][0]["cwd"] == "/home/me/research/a"


def test_the_expert_works_even_on_a_conversation_that_began_as_chat():
    settings = turn_settings(
        "chat",
        directory="/home/me/research/a",
        environment_key="user-pc",
        environment_online=True,
        approval="on-request",
        expert=True,
    )
    assert settings["sandboxPolicy"]["type"] == "workspaceWrite"
    assert settings["approvalPolicy"] == "on-request"


def test_work_and_expert_turns_cannot_be_built_without_a_project():
    for kind, expert in (("work", False), ("chat", True)):
        with pytest.raises(ValueError):
            turn_settings(
                kind,
                directory=None,
                environment_key=None,
                environment_online=True,
                approval="on-request",
                expert=expert,
            )


def test_an_approval_policy_codex_no_longer_knows_falls_back():
    assert approval_policy("never") == "never"
    assert approval_policy("untrusted") == "untrusted"
    for unknown in ("on-failure", "", None, "always"):
        assert approval_policy(unknown) == "on-request"
    settings = turn_settings(
        "work",
        directory="/p",
        environment_key="user-pc",
        environment_online=True,
        approval="on-failure",
    )
    assert settings["approvalPolicy"] == "on-request"


def test_the_thread_starts_with_the_settings_of_its_kind():
    quiet = {"features.multi_agent": False, "features.goals": False}
    assert thread_settings(
        "chat", directory=None, environment_key=None, approval="untrusted"
    ) == {
        "environments": [],
        "sandbox": "read-only",
        "approvalPolicy": "never",
        "config": quiet,
    }
    assert thread_settings(
        "chat", directory="/p", environment_key="user-pc", approval="untrusted"
    ) == {
        "environments": [{"environmentId": "user-pc", "cwd": "/p"}],
        "sandbox": "read-only",
        "approvalPolicy": "never",
        "config": quiet,
        "cwd": "/p",
    }
    assert thread_settings(
        "work",
        directory="/p",
        environment_key="user-pc",
        approval="untrusted",
        model="kimi-k3",
    ) == {
        "environments": [{"environmentId": "user-pc", "cwd": "/p"}],
        "sandbox": "workspace-write",
        "approvalPolicy": "untrusted",
        "config": quiet,
        "cwd": "/p",
        "model": "kimi-k3",
    }
    with pytest.raises(ValueError):
        thread_settings("work", directory=None, environment_key=None, approval=None)


@pytest.mark.parametrize(
    ("kind", "directory", "online", "expert", "expected"),
    [
        ("chat", None, True, False, "chat"),
        ("chat", "/p", False, False, "chat"),  # 机器不在线：和没有项目一样，动不了手
        ("chat", "/p", True, False, "chat_in_project"),
        ("work", "/p", True, False, "work"),
        ("work", "/p", False, False, "work"),
        ("chat", "/p", True, True, "expert"),
        ("work", "/p", True, True, "expert"),
    ],
)
def test_the_mode_of_a_turn(kind, directory, online, expert, expected):
    assert (
        mode_of(
            kind,
            directory=directory,
            environment_key="user-pc" if directory else None,
            environment_online=online,
            expert=expert,
        )
        == expected
    )


def test_work_and_expert_need_a_project():
    for kind, expert in (("work", False), ("chat", True)):
        with pytest.raises(ValueError):
            mode_of(
                kind,
                directory=None,
                environment_key=None,
                environment_online=True,
                expert=expert,
            )


def test_the_note_says_what_the_model_has_in_hand():
    chat = mode_note("chat", directory=None)
    assert "no shell" in chat and "/p" not in chat
    reading = mode_note("chat_in_project", directory="/p")
    assert "shell tool IS available" in reading and "read-only" in reading
    assert "/p" in reading
    for mode in ("work", "expert"):
        writing = mode_note(mode, directory="/p")
        assert "shell tool IS available" in writing and "/p" in writing
        assert "read-only" not in writing
    # 说明是给模型看的，开头有固定的标记，页面据此不把它当成用户的话
    assert all(n.startswith("[workbench] ") for n in (chat, reading))
