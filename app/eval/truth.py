"""真值执行与比对（F-EVAL-01）：候选 SQL 与真值 SQL 都在同一数据版本上跑，按期望列投影、按键列排序、按容差比值。

两种方言：`sqlite` 直接读数据集文件；`duckdb` 把数据集原样装进内存里的 DuckDB 再跑。
知识服务开着语义层时，专家写的是 DuckDB 方言的 SQL，评测就要用同一种方言重跑它。
表名、字段名、类型与数据集文件一致（整数、小数、文字照搬），所以两种方言下真值相同。
"""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any, Literal

from .cases import TruthQuery

Rows = list[dict[str, Any]]
Dialect = Literal["sqlite", "duckdb"]
_DUCKDB_TYPES = (("INT", "BIGINT"), ("REAL", "DOUBLE"), ("FLOA", "DOUBLE"))


def _sql_text(value: str) -> str:
    """SQL 字符串字面量。"""
    return "'" + value.replace("'", "''") + "'"


class QueryFailed(Exception):
    """候选或真值 SQL 在这个数据集上跑不了。两种方言下都用这一个异常。"""


class TruthStore:
    def __init__(self, db_path: Path, *, dialect: Dialect = "sqlite") -> None:
        if dialect not in ("sqlite", "duckdb"):
            raise ValueError(f"unknown dialect: {dialect}")
        self.db_path = Path(db_path)
        self.dialect: Dialect = dialect
        self._cache: dict[str, tuple[list[str], Rows]] = {}
        self._duck: Any = None

    def data_version(self) -> str:
        cols, rows = self.query("SELECT key, value FROM dataset_metadata")
        for r in rows:
            if r["key"] == "data_snapshot_id":
                return str(r["value"])
        raise RuntimeError("dataset has no data_snapshot_id")

    def query(self, sql: str) -> tuple[list[str], Rows]:
        body = sql.strip().rstrip(";")
        if self.dialect == "duckdb":
            return self._query_duckdb(body)
        try:
            with sqlite3.connect(
                f"file:{self.db_path.resolve()}?mode=ro", uri=True
            ) as conn:
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA query_only = ON")
                cur = conn.execute(body)
                columns = [str(d[0]) for d in cur.description or ()]
                rows = [dict(r) for r in cur.fetchall()]
        except sqlite3.Error as exc:
            raise QueryFailed(str(exc)) from exc
        return columns, rows

    def _query_duckdb(self, body: str) -> tuple[list[str], Rows]:
        import duckdb  # 只有评测用；不是产品运行时的依赖

        try:
            cursor = self._duckdb().cursor()
            try:
                cursor.execute(body)
                columns = [str(d[0]) for d in cursor.description or ()]
                fetched = cursor.fetchall()
            finally:
                cursor.close()
        except duckdb.Error as exc:
            raise QueryFailed(str(exc)) from exc
        if len(set(columns)) != len(columns):
            raise QueryFailed("duplicate column names in the result")
        return columns, [dict(zip(columns, row, strict=True)) for row in fetched]

    def _duckdb(self) -> Any:
        """数据集原样装进内存库：表名不变，类型照搬；装好后关掉对外访问。

        每张表先写成临时 CSV，再由 DuckDB 自己的读取器按建好的列类型读进来：DuckDB 把
        Python 值逐个转进去很慢（几万行要几十秒），读 CSV 不到一秒。字符串一律加引号、
        空值写成不加引号的空字段，读时不把加引号的空串当空值，所以空串与空值不会混；
        小数按 Python 的最短精确写法写出，读回来逐位相同。
        """
        if self._duck is not None:
            return self._duck
        import duckdb

        db = duckdb.connect(":memory:")
        with (
            sqlite3.connect(f"file:{self.db_path.resolve()}?mode=ro", uri=True) as src,
            tempfile.TemporaryDirectory(prefix="eval-truth-") as tmp,
        ):
            tables = [
                str(r[0])
                for r in src.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for index, table in enumerate(tables):
                info = src.execute(f'PRAGMA table_info("{table}")').fetchall()
                names = [str(c[1]) for c in info]
                kinds = [
                    next(
                        (to for key, to in _DUCKDB_TYPES if key in str(c[2]).upper()),
                        "VARCHAR",
                    )
                    for c in info
                ]
                spec = ", ".join(
                    f'"{n}" {k}' for n, k in zip(names, kinds, strict=True)
                )
                db.execute(f'CREATE TABLE "{table}" ({spec})')
                path = Path(tmp) / f"{index}.csv"
                with path.open("w", newline="", encoding="utf-8") as out:
                    writer = csv.writer(out, quoting=csv.QUOTE_STRINGS)
                    writer.writerows(src.execute(f'SELECT * FROM "{table}"'))  # noqa: S608
                columns = ", ".join(
                    f"{_sql_text(n)}: {_sql_text(k)}"
                    for n, k in zip(names, kinds, strict=True)
                )
                db.execute(
                    f'INSERT INTO "{table}" SELECT * FROM read_csv('  # noqa: S608
                    f"{_sql_text(str(path))}, header = false, delim = ',', "
                    "quote = '\"', escape = '\"', allow_quoted_nulls = false, "
                    f"columns = {{{columns}}})"
                )
        db.execute("SET enable_external_access=false")
        db.execute("SET lock_configuration=true")
        self._duck = db
        return db

    def close(self) -> None:
        if self._duck is not None:
            self._duck.close()
            self._duck = None

    def truth(self, q: TruthQuery) -> tuple[list[str], Rows]:
        if q.query_id not in self._cache:
            self._cache[q.query_id] = self.query(q.sql)
        return self._cache[q.query_id]


def project(
    rows: Rows,
    expected_columns: tuple[str, ...],
    key_columns: tuple[str, ...],
    *,
    distinct: bool = False,
) -> Rows:
    projected = [{c: row.get(c) for c in expected_columns} for row in rows]
    if distinct:
        seen: set[str] = set()
        unique: Rows = []
        for row in projected:
            mark = json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
            if mark not in seen:
                seen.add(mark)
                unique.append(row)
        projected = unique
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
    left = project(
        candidate_rows, q.expected_columns, q.key_columns, distinct=q.distinct
    )
    right = project(truth_rows, q.expected_columns, q.key_columns, distinct=q.distinct)
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
        project(rows, q.expected_columns, q.key_columns, distinct=q.distinct),
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(canon.encode()).hexdigest()[:16]
