"""登记 CLI：按邮箱找用户，幂等登记环境与沙箱，不回显令牌。"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from test_workbench_ledger_db import db as db  # noqa: F401

from app.cli.workbench_register import parse_args, register


async def test_register_is_idempotent_and_needs_a_logged_in_user(db, capsys):  # noqa: F811
    email = f"demo-{uuid.uuid4().hex[:6]}@example.com"
    args = parse_args(
        [
            "--email",
            email,
            "--root",
            "/home/u/research",
            "--sandbox-url",
            "ws://sandbox-demo.sandbox-pool.svc:47800",
            "--token-ref",
            "env:SANDBOX_DEMO_APP_SERVER_TOKEN",
        ]
    )
    with pytest.raises(SystemExit, match="log in once first"):
        await register(db, args)
    async with db() as s:
        await s.execute(
            text(
                "insert into auth_user (id, issuer, subject, username, email, display_name, roles, scopes) "
                "values (:id, 'https://casdoor.test', :sub, 'demo', :email, 'Demo', '[]'::jsonb, '[]'::jsonb)"
            ),
            {"id": str(uuid.uuid4()), "sub": f"sub-{email}", "email": email},
        )
        await s.commit()
    first = await register(db, args)
    second = await register(db, args)
    assert first["environment_id"] == second["environment_id"]
    assert first["sandbox_id"] == second["sandbox_id"]
    assert not first["environment_reused"] and second["environment_reused"]
    assert not first["sandbox_reused"] and second["sandbox_reused"]
    assert "SANDBOX_DEMO_APP_SERVER_TOKEN" not in str(first)
