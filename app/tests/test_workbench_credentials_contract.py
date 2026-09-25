"""凭据列表的字段集合是网页契约的一部分（网页用 strict 校验，多一个字段整页列表就空；KIND 2026-09-26 踩过）。
改这里的字段，要同时改 investment-web-frontend 的 contracts/workbench.ts credentialSchema。"""

from __future__ import annotations

import re
from pathlib import Path

REPO = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "infrastructure"
    / "workbench"
    / "repository.py"
)
WEB_FIELDS = {
    "id",
    "sandbox_id",
    "provider",
    "hint",
    "status",
    "created_at",
    "revoked_at",
}


def _select_fields(func: str) -> set[str]:
    src = REPO.read_text(encoding="utf-8")
    body = src[src.index(f"async def {func}(") :]
    body = body[: body.index("\n    async def ", 1)]
    m = re.search(r"select (.+?)\s+from workbench_credentials", body, re.S)
    assert m, func
    return {c.strip() for c in m.group(1).split(",")}


def test_list_fields_match_the_web_contract():
    fields = _select_fields("list_credentials")
    assert "ciphertext" not in fields
    assert fields == WEB_FIELDS, (
        f"web credentialSchema must allow exactly {sorted(fields)}"
    )
