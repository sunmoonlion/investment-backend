"""工作台仓储。与旧 AgentRepository 平行；不共用 agent_* 表。

约定：
- 每个公开方法要么在调用方的 transaction() 里跑，要么自己 @atomic；
- 事件游标按会话用咨询锁串行分配（与 session_events 同法）；
- 状态改动一律 compare-and-swap（where state_version = :expected），失败抛 StaleStateVersion；
- 不在这里判断业务合法性（转换表在 domain），这里只保证原子性。
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from decimal import Decimal
from functools import wraps
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.application.dto.outbox import OutboxEvent
from app.domain.workbench.errors import NotFound, StaleStateVersion
from app.infrastructure.repositories.outbox import SqlOutboxRepository


def atomic(method):
    @wraps(method)
    async def wrapped(self, *args, **kwargs):
        async with self.transaction():
            return await method(self, *args, **kwargs)

    return wrapped


def _j(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


class WorkbenchRepository:
    def __init__(self, session: AsyncSession):
        self.session = session
        self._depth = 0

    @asynccontextmanager
    async def transaction(self):
        outer = self._depth == 0
        self._depth += 1
        try:
            yield
            if outer:
                await self.session.commit()
        except BaseException:
            if outer:
                await self.session.rollback()
            raise
        finally:
            self._depth -= 1

    # ---------- environments / sandboxes ----------
    async def register_environment(
        self,
        *,
        owner_actor_id: str,
        name: str,
        agent_version: str | None,
        codex_version: str | None,
        roots: list[str],
        ceiling: dict,
    ) -> str:
        env_id = str(uuid.uuid4())
        await self.session.execute(
            text("""insert into workbench_environments (id, owner_actor_id, name, agent_version, codex_version, roots, ceiling, status, last_seen_at)
                    values (:id, :owner, :name, :av, :cv, cast(:roots as jsonb), cast(:ceiling as jsonb), 'online', now())"""),
            {
                "id": env_id,
                "owner": owner_actor_id,
                "name": name,
                "av": agent_version,
                "cv": codex_version,
                "roots": _j(roots),
                "ceiling": _j(ceiling),
            },
        )
        return env_id

    async def list_environments(self, *, owner_actor_id: str) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text(
                "select * from workbench_environments where owner_actor_id = :o order by created_at"
            ),
            {"o": owner_actor_id},
        )
        return [dict(m) for m in r.mappings().all()]

    async def get_environment(
        self, environment_id: str, *, owner_actor_id: str | None = None
    ) -> dict[str, Any]:
        r = await self.session.execute(
            text("select * from workbench_environments where id = :id"),
            {"id": environment_id},
        )
        row = r.mappings().first()
        if row is None or (
            owner_actor_id is not None and str(row["owner_actor_id"]) != owner_actor_id
        ):
            raise NotFound("environment not found", environment_id=environment_id)
        return dict(row)

    async def set_environment_status(self, environment_id: str, status: str) -> None:
        await self.session.execute(
            text(
                "update workbench_environments set status = :s, last_seen_at = now(), updated_at = now() where id = :id"
            ),
            {"s": status, "id": environment_id},
        )

    async def register_sandbox(
        self,
        *,
        owner_actor_id: str,
        app_server_url: str,
        token_ref: str,
        codex_version: str | None,
    ) -> str:
        sb_id = str(uuid.uuid4())
        await self.session.execute(
            text("""insert into workbench_sandboxes (id, owner_actor_id, app_server_url, token_ref, codex_version, status)
                    values (:id, :owner, :url, :ref, :cv, 'registered')"""),
            {
                "id": sb_id,
                "owner": owner_actor_id,
                "url": app_server_url,
                "ref": token_ref,
                "cv": codex_version,
            },
        )
        return sb_id

    async def get_sandbox(
        self, sandbox_id: str, *, owner_actor_id: str | None = None
    ) -> dict[str, Any]:
        r = await self.session.execute(
            text("select * from workbench_sandboxes where id = :id"), {"id": sandbox_id}
        )
        row = r.mappings().first()
        if row is None or (
            owner_actor_id is not None and str(row["owner_actor_id"]) != owner_actor_id
        ):
            raise NotFound("sandbox not found", sandbox_id=sandbox_id)
        return dict(row)

    async def list_sandboxes(self, *, owner_actor_id: str) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text(
                "select * from workbench_sandboxes where owner_actor_id = :o order by created_at"
            ),
            {"o": owner_actor_id},
        )
        return [dict(m) for m in r.mappings().all()]

    # ---------- sessions ----------
    async def create_session(
        self,
        *,
        owner_actor_id: str,
        environment_id: str,
        sandbox_id: str,
        project_root: str,
        thread_settings: dict,
    ) -> str:
        sid = str(uuid.uuid4())
        await self.session.execute(
            text("""insert into workbench_sessions (id, owner_actor_id, environment_id, sandbox_id, project_root, thread_settings)
                    values (:id, :owner, :env, :sb, :root, cast(:ts as jsonb))"""),
            {
                "id": sid,
                "owner": owner_actor_id,
                "env": environment_id,
                "sb": sandbox_id,
                "root": project_root,
                "ts": _j(thread_settings),
            },
        )
        return sid

    async def get_session(
        self,
        session_id: str,
        *,
        owner_actor_id: str | None = None,
        for_update: bool = False,
    ) -> dict[str, Any]:
        sql = "select * from workbench_sessions where id = :id" + (
            " for update" if for_update else ""
        )
        r = await self.session.execute(text(sql), {"id": session_id})
        row = r.mappings().first()
        if row is None or (
            owner_actor_id is not None and str(row["owner_actor_id"]) != owner_actor_id
        ):
            raise NotFound("session not found", session_id=session_id)
        return dict(row)

    async def list_sessions(self, *, owner_actor_id: str) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text(
                "select * from workbench_sessions where owner_actor_id = :o order by last_active_at desc"
            ),
            {"o": owner_actor_id},
        )
        return [dict(m) for m in r.mappings().all()]

    async def set_session_thread(self, session_id: str, thread_id: str) -> None:
        await self.session.execute(
            text(
                "update workbench_sessions set thread_id = :t, last_active_at = now() where id = :id"
            ),
            {"t": thread_id, "id": session_id},
        )

    async def touch_session(self, session_id: str) -> None:
        await self.session.execute(
            text("update workbench_sessions set last_active_at = now() where id = :id"),
            {"id": session_id},
        )

    async def cas_session_wheel(
        self,
        session_id: str,
        *,
        expected_version: int,
        wheel: str,
        active_task_id: str | None,
    ) -> int:
        r = await self.session.execute(
            text("""update workbench_sessions set wheel = :w, active_task_id = :t, state_version = state_version + 1, last_active_at = now()
                    where id = :id and state_version = :v returning state_version"""),
            {"w": wheel, "t": active_task_id, "id": session_id, "v": expected_version},
        )
        v = r.scalar_one_or_none()
        if v is None:
            raise StaleStateVersion(
                "session changed concurrently", session_id=session_id
            )
        return int(v)

    # ---------- events ----------
    async def append_event(
        self,
        *,
        session_id: str,
        kind: str,
        event_type: str,
        payload: dict,
        task_id: str | None = None,
        attempt_id: str | None = None,
    ) -> dict[str, Any]:
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:s, 1))"),
            {"s": session_id},
        )
        r = await self.session.execute(
            text(
                "select coalesce(max(cursor), 0) + 1 from workbench_session_events where session_id = :s"
            ),
            {"s": session_id},
        )
        cursor = int(r.scalar_one())
        eid = str(uuid.uuid4())
        await self.session.execute(
            text("""insert into workbench_session_events (id, session_id, cursor, kind, event_type, payload, task_id, attempt_id)
                    values (:id, :s, :c, :k, :t, cast(:p as jsonb), :task, :attempt)"""),
            {
                "id": eid,
                "s": session_id,
                "c": cursor,
                "k": kind,
                "t": event_type,
                "p": _j(payload),
                "task": task_id,
                "attempt": attempt_id,
            },
        )
        event = {
            "id": eid,
            "session_id": session_id,
            "cursor": cursor,
            "kind": kind,
            "type": event_type,
            "payload": payload,
            "task_id": task_id,
            "attempt_id": attempt_id,
        }
        await SqlOutboxRepository().enqueue(
            self.session,
            OutboxEvent(
                topic="workbench.session_event",
                aggregate_key=session_id,
                deduplication_key=f"evt:{eid}",
                payload=event,
            ),
        )
        return event

    async def list_events(
        self, *, session_id: str, after_cursor: int = 0, limit: int = 500
    ) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text("""select id, session_id, cursor, kind, event_type as type, payload, task_id, attempt_id, created_at
                    from workbench_session_events where session_id = :s and cursor > :c order by cursor asc limit :l"""),
            {"s": session_id, "c": after_cursor, "l": limit},
        )
        out = []
        for m in r.mappings().all():
            d = dict(m)
            for k in ("id", "session_id", "task_id", "attempt_id"):
                if d.get(k) is not None:
                    d[k] = str(d[k])
            out.append(d)
        return out

    # ---------- tasks ----------
    async def find_task_by_idempotency(
        self, *, tenant: str, owner_actor_id: str, profile_id: str, idempotency_key: str
    ) -> dict[str, Any] | None:
        r = await self.session.execute(
            text(
                """select * from workbench_tasks where tenant = :t and owner_actor_id = :o and profile_id = :p and idempotency_key = :k"""
            ),
            {"t": tenant, "o": owner_actor_id, "p": profile_id, "k": idempotency_key},
        )
        row = r.mappings().first()
        return dict(row) if row else None

    async def insert_task(self, task: dict[str, Any]) -> None:
        cols = list(task.keys())
        json_cols = {
            "original_input",
            "acceptance_contract",
            "execution_policy",
            "budget",
            "rejection",
        }
        values = ", ".join(
            f"cast(:{c} as jsonb)" if c in json_cols else f":{c}" for c in cols
        )
        params = {
            c: (_j(task[c]) if c in json_cols and task[c] is not None else task[c])
            for c in cols
        }
        await self.session.execute(
            text(f"insert into workbench_tasks ({', '.join(cols)}) values ({values})"),
            params,
        )

    async def get_task(
        self,
        task_id: str,
        *,
        owner_actor_id: str | None = None,
        for_update: bool = False,
    ) -> dict[str, Any]:
        sql = "select * from workbench_tasks where id = :id" + (
            " for update" if for_update else ""
        )
        r = await self.session.execute(text(sql), {"id": task_id})
        row = r.mappings().first()
        if row is None or (
            owner_actor_id is not None and str(row["owner_actor_id"]) != owner_actor_id
        ):
            raise NotFound("task not found", task_id=task_id)
        return dict(row)

    async def list_tasks(
        self, *, owner_actor_id: str, session_id: str | None = None
    ) -> list[dict[str, Any]]:
        sql = (
            "select * from workbench_tasks where owner_actor_id = :o"
            + (" and session_id = :s" if session_id else "")
            + " order by created_at desc"
        )
        r = await self.session.execute(
            text(sql), {"o": owner_actor_id, "s": session_id}
        )
        return [dict(m) for m in r.mappings().all()]

    async def list_nonterminal_tasks(self) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text(
                "select * from workbench_tasks where state not in ('SUCCEEDED','REJECTED','FAILED','CANCELLED') order by created_at"
            )
        )
        return [dict(m) for m in r.mappings().all()]

    async def cas_task(
        self, task_id: str, *, expected_version: int, **fields: Any
    ) -> int:
        """比较交换更新 Task 的若干列；返回新 state_version。"""
        json_cols = {"budget", "acceptance_contract", "execution_policy", "rejection"}
        sets = ", ".join(
            f"{k} = cast(:{k} as jsonb)" if k in json_cols else f"{k} = :{k}"
            for k in fields
        )
        params = {
            k: (_j(v) if k in json_cols and v is not None else v)
            for k, v in fields.items()
        }
        params.update({"id": task_id, "v": expected_version})
        r = await self.session.execute(
            text(
                f"update workbench_tasks set {sets}, state_version = state_version + 1, updated_at = now() where id = :id and state_version = :v returning state_version"
            ),
            params,
        )
        v = r.scalar_one_or_none()
        if v is None:
            raise StaleStateVersion("task changed concurrently", task_id=task_id)
        return int(v)

    # ---------- attempts ----------
    async def insert_attempt(self, attempt: dict[str, Any]) -> None:
        json_cols = {
            "input_artifact_versions",
            "turn_ids",
            "budget_allocated",
            "budget_consumed",
            "output_artifacts",
            "refs",
        }
        cols = list(attempt.keys())
        values = ", ".join(
            f"cast(:{c} as jsonb)" if c in json_cols else f":{c}" for c in cols
        )
        params = {c: (_j(attempt[c]) if c in json_cols else attempt[c]) for c in cols}
        await self.session.execute(
            text(
                f"insert into workbench_attempts ({', '.join(cols)}) values ({values})"
            ),
            params,
        )

    async def get_attempt(
        self, attempt_id: str, *, for_update: bool = False
    ) -> dict[str, Any]:
        r = await self.session.execute(
            text(
                "select * from workbench_attempts where id = :id"
                + (" for update" if for_update else "")
            ),
            {"id": attempt_id},
        )
        row = r.mappings().first()
        if row is None:
            raise NotFound("attempt not found", attempt_id=attempt_id)
        return dict(row)

    async def update_attempt(self, attempt_id: str, **fields: Any) -> None:
        json_cols = {
            "input_artifact_versions",
            "turn_ids",
            "budget_allocated",
            "budget_consumed",
            "output_artifacts",
            "refs",
        }
        sets = ", ".join(
            f"{k} = cast(:{k} as jsonb)" if k in json_cols else f"{k} = :{k}"
            for k in fields
        )
        params = {k: (_j(v) if k in json_cols else v) for k, v in fields.items()}
        params["id"] = attempt_id
        await self.session.execute(
            text(
                f"update workbench_attempts set {sets}, updated_at = now() where id = :id"
            ),
            params,
        )

    async def list_attempts(self, task_id: str) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text(
                "select * from workbench_attempts where task_id = :t order by created_at"
            ),
            {"t": task_id},
        )
        return [dict(m) for m in r.mappings().all()]

    # ---------- interactions ----------
    async def insert_interaction(
        self,
        *,
        session_id: str,
        task_id: str | None,
        attempt_id: str | None,
        kind: str,
        prompt: dict,
        subject_digest: str | None,
        token: str,
        target_state_version: int | None,
        expires_at,
    ) -> str:
        iid = str(uuid.uuid4())
        await self.session.execute(
            text("""insert into workbench_interactions (id, session_id, task_id, attempt_id, kind, prompt, subject_digest, token_hash, target_state_version, expires_at)
                    values (:id, :s, :t, :a, :k, cast(:p as jsonb), :d, :h, :v, :e)"""),
            {
                "id": iid,
                "s": session_id,
                "t": task_id,
                "a": attempt_id,
                "k": kind,
                "p": _j(prompt),
                "d": subject_digest,
                "h": token_hash(token),
                "v": target_state_version,
                "e": expires_at,
            },
        )
        return iid

    async def get_interaction(
        self, interaction_id: str, *, for_update: bool = False
    ) -> dict[str, Any]:
        r = await self.session.execute(
            text(
                "select * from workbench_interactions where id = :id"
                + (" for update" if for_update else "")
            ),
            {"id": interaction_id},
        )
        row = r.mappings().first()
        if row is None:
            raise NotFound("interaction not found", interaction_id=interaction_id)
        return dict(row)

    async def list_interactions(
        self, *, session_id: str, status: str | None = "pending"
    ) -> list[dict[str, Any]]:
        sql = (
            "select * from workbench_interactions where session_id = :s"
            + (" and status = :st" if status else "")
            + " order by created_at"
        )
        r = await self.session.execute(text(sql), {"s": session_id, "st": status})
        return [dict(m) for m in r.mappings().all()]

    async def consume_interaction(
        self, interaction_id: str, *, response: dict, responded_by: str
    ) -> bool:
        """原子消费：只有 pending 且未过期的那一行会被改；返回是否消费成功。"""
        r = await self.session.execute(
            text("""update workbench_interactions set status = 'consumed', response = cast(:r as jsonb), consumed_at = now(), responded_by = :by
                    where id = :id and status = 'pending' and (expires_at is null or expires_at > now()) returning id"""),
            {"r": _j(response), "by": responded_by, "id": interaction_id},
        )
        return r.scalar_one_or_none() is not None

    async def cancel_pending_interactions(self, *, task_id: str, reason: str) -> int:
        r = await self.session.execute(
            text("""update workbench_interactions set status = 'cancelled', response = cast(:r as jsonb), consumed_at = now()
                    where task_id = :t and status = 'pending' returning id"""),
            {"r": _j({"cancelled": reason}), "t": task_id},
        )
        return len(r.all())

    # ---------- artifacts ----------
    async def put_artifact(
        self,
        *,
        task_id: str,
        attempt_id: str | None,
        name: str,
        kind: str,
        content: Any,
        workspace_path: str | None = None,
    ) -> dict[str, Any]:
        body = _j(content)
        digest = hashlib.sha256(body.encode()).hexdigest()
        await self.session.execute(
            text("select pg_advisory_xact_lock(hashtextextended(:k, 2))"),
            {"k": f"{task_id}:{name}"},
        )
        r = await self.session.execute(
            text(
                "select coalesce(max(version), 0) + 1 from workbench_artifacts where task_id = :t and name = :n"
            ),
            {"t": task_id, "n": name},
        )
        version = int(r.scalar_one())
        aid = str(uuid.uuid4())
        await self.session.execute(
            text("""insert into workbench_artifacts (id, task_id, attempt_id, name, version, kind, content, digest, workspace_path)
                    values (:id, :t, :a, :n, :v, :k, cast(:c as jsonb), :d, :w)"""),
            {
                "id": aid,
                "t": task_id,
                "a": attempt_id,
                "n": name,
                "v": version,
                "k": kind,
                "c": body,
                "d": digest,
                "w": workspace_path,
            },
        )
        return {
            "id": aid,
            "task_id": task_id,
            "name": name,
            "version": version,
            "kind": kind,
            "digest": digest,
        }

    async def get_artifact(
        self, *, task_id: str, name: str, version: int | None = None
    ) -> dict[str, Any] | None:
        sql = "select * from workbench_artifacts where task_id = :t and name = :n" + (
            " and version = :v" if version else " order by version desc limit 1"
        )
        r = await self.session.execute(
            text(sql), {"t": task_id, "n": name, "v": version}
        )
        row = r.mappings().first()
        return dict(row) if row else None

    async def list_artifacts(self, task_id: str) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text(
                "select id, task_id, attempt_id, name, version, kind, digest, workspace_path, created_at from workbench_artifacts where task_id = :t order by name, version"
            ),
            {"t": task_id},
        )
        return [dict(m) for m in r.mappings().all()]

    # ---------- budget ----------
    async def ledger_append(
        self,
        *,
        task_id: str,
        attempt_id: str | None,
        entry: str,
        amount: Decimal,
        tokens: dict | None = None,
        note: str | None = None,
        actor_id: str | None = None,
    ) -> None:
        await self.session.execute(
            text("""insert into workbench_budget_ledger (task_id, attempt_id, entry, amount, tokens, note, actor_id)
                    values (:t, :a, :e, :m, cast(:k as jsonb), :n, :by)"""),
            {
                "t": task_id,
                "a": attempt_id,
                "e": entry,
                "m": amount,
                "k": _j(tokens or {}),
                "n": note,
                "by": actor_id,
            },
        )

    async def ledger_list(self, task_id: str) -> list[dict[str, Any]]:
        r = await self.session.execute(
            text(
                "select * from workbench_budget_ledger where task_id = :t order by id"
            ),
            {"t": task_id},
        )
        return [dict(m) for m in r.mappings().all()]

    # ---------- approvals ----------
    async def log_approval(
        self,
        *,
        session_id: str,
        task_id: str | None,
        request_id: str,
        method: str,
        summary: dict,
        decision: str,
        source: str,
        interaction_id: str | None = None,
    ) -> None:
        await self.session.execute(
            text("""insert into workbench_approval_log (session_id, task_id, request_id, method, summary, decision, source, interaction_id)
                    values (:s, :t, :r, :m, cast(:sm as jsonb), :d, :src, :i)"""),
            {
                "s": session_id,
                "t": task_id,
                "r": request_id,
                "m": method,
                "sm": _j(summary),
                "d": decision,
                "src": source,
                "i": interaction_id,
            },
        )
