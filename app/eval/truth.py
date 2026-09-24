"""真值执行与比对（F-EVAL-01）：候选 SQL 与真值 SQL 都在同一数据版本上跑，按期望列投影、按键列排序、按容差比值。"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .cases import TruthQuery

Rows = list[dict[str, Any]]


class TruthStore:
    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self._cache: dict[str, tuple[list[str], Rows]] = {}

    def data_version(self) -> str:
        cols, rows = self.query("SELECT key, value FROM dataset_metadata")
        for r in rows:
            if r["key"] == "data_snapshot_id":
                return str(r["value"])
        raise RuntimeError("dataset has no data_snapshot_id")

    def query(self, sql: str) -> tuple[list[str], Rows]:
        with sqlite3.connect(
            f"file:{self.db_path.resolve()}?mode=ro", uri=True
        ) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            cur = conn.execute(sql.strip().rstrip(";"))
            columns = [str(d[0]) for d in cur.description or ()]
            rows = [dict(r) for r in cur.fetchall()]
        return columns, rows

    def truth(self, q: TruthQuery) -> tuple[list[str], Rows]:
        if q.query_id not in self._cache:
            self._cache[q.query_id] = self.query(q.sql)
        return self._cache[q.query_id]


def project(
    rows: Rows, expected_columns: tuple[str, ...], key_columns: tuple[str, ...]
) -> Rows:
    projected = [{c: row.get(c) for c in expected_columns} for row in rows]
    keys = key_columns or expected_columns
    projected.sort(
        key=lambda row: tuple(json.dumps(row.get(c), ensure_ascii=False) for c in keys)
    )
    return projected


def same_value(left: Any, right: Any, tolerance: float) -> bool:
    if isinstance(left, int | float) and not isinstance(left, bool):
        if isinstance(right, int | float) and not isinstance(right, bool):
            return abs(float(left) - float(right)) <= tolerance
    return left == right


def same_result(
    candidate_columns: list[str],
    candidate_rows: Rows,
    truth_columns: list[str],
    truth_rows: Rows,
    q: TruthQuery,
) -> tuple[bool, str]:
    """返回 (是否相同, 不同的原因)。原因是给报告看的，不是给模型看的。"""
    missing = [c for c in q.expected_columns if c not in candidate_columns]
    if missing:
        return False, f"columns missing: {', '.join(missing)}"
    if not set(q.expected_columns) <= set(truth_columns):
        return False, "truth query does not produce expected columns"
    left = project(candidate_rows, q.expected_columns, q.key_columns)
    right = project(truth_rows, q.expected_columns, q.key_columns)
    if len(left) != len(right):
        return False, f"row count {len(left)} != {len(right)}"
    for i, (a, b) in enumerate(zip(left, right, strict=True)):
        for c in q.expected_columns:
            if not same_value(a[c], b[c], q.tolerance):
                return False, f"row {i} column {c}: {a[c]!r} != {b[c]!r}"
    return True, ""


def fingerprint(rows: Rows, q: TruthQuery) -> str:
    """result_fingerprint：投影 + 排序后的规范 JSON 摘要；真值与候选各算一次即可比对。"""
    canon = json.dumps(
        project(rows, q.expected_columns, q.key_columns),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canon.encode()).hexdigest()[:16]
