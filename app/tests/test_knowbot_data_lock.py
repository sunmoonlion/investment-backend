"""读用户数据的契约（knowlab 定，0027 §3.5）：锁与提供方 schema 一致，我们发的请求合 schema。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import httpx
import pytest

from app.domain.workbench.errors import UserDataUnavailable
from app.infrastructure.external.knowlab_data_client import KnowlabDataClient

PARENT = Path(__file__).resolve().parents[3]
LOCK = PARENT / "contracts/knowlab-knowbot-data-lock.json"
PURPOSES = [
    {"kind": "test", "run_id": "knowlab-run-0001"},
    {
        "kind": "knowbot",
        "owner_subject": "user-123",
        "knowbot_id": "0b8a6c3e-1f2d-4c5b-9a7e-6d5c4b3a2f10",
        "version": "1",
        "digest": "sha256:" + "d" * 64,
    },
]


def _lock() -> dict:
    if not LOCK.exists():
        pytest.skip("parent contracts/ is not checked out")
    return json.loads(LOCK.read_text(encoding="utf-8"))


def _schema(lock: dict, name: str) -> dict:
    path = PARENT.parent / "knowlab-app" / lock["schemas"][name]["path"]
    if not path.exists():
        pytest.skip("knowlab-app is not checked out next to investment-app")
    return json.loads(path.read_text(encoding="utf-8"))


def test_every_pinned_schema_matches_the_provider() -> None:
    lock = _lock()
    assert lock["provider"] == "knowlab-app" and lock["major_version"] == 1
    for name, pinned in lock["schemas"].items():
        path = PARENT.parent / "knowlab-app" / pinned["path"]
        if not path.exists():
            pytest.skip("knowlab-app is not checked out next to investment-app")
        assert hashlib.sha256(path.read_bytes()).hexdigest() == pinned["sha256"], name


@pytest.mark.parametrize("purpose", PURPOSES)
async def test_the_requests_we_send_fit_the_contract(purpose) -> None:
    lock = _lock()
    jsonschema = pytest.importorskip("jsonschema")
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        if request.url.path.endswith("/list"):
            return httpx.Response(
                200, json={"contract": "knowbot-data/v1", "datasets": []}
            )
        return httpx.Response(
            200,
            json={"contract": "knowbot-data/v1", "dataset_id": "d" * 8, "text": ""},
        )

    from test_workbench_knowbot_data import ENV, FakeOidc

    client = KnowlabDataClient(
        ENV, oidc_client=FakeOidc(), transport=httpx.MockTransport(handler)
    )
    await client.list(purpose)
    with pytest.raises(UserDataUnavailable):  # 回来的编号不是问的那一份：按契约外处理
        await client.read(purpose, "0b8a6c3e-1f2d-4c5b-9a7e-6d5c4b3a2f11", 2)
    jsonschema.validate(sent[0], _schema(lock, "data-list-request.schema.json"))
    jsonschema.validate(sent[1], _schema(lock, "data-read-request.schema.json"))
