"""单案判定（F-EVAL-05）：只有 pass / fail / undecidable。

- 质量：每条真值查询都被候选的某条 SQL 覆盖（同一数据版本上跑出同样的投影结果）；
- 引用：候选报出的 data_version 与数据集一致；
- 不可判：臂没跑完（错误、超时、等人）、数据集版本与案例冻结的版本不一致、没有任何 SQL。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

from .cases import Case
from .truth import TruthStore, same_result

Level = Literal["pass", "fail", "undecidable"]


@dataclass
class ArmOutput:
    case_id: str
    arm: str
    sqls: list[str] = field(default_factory=list)
    data_versions: list[str] = field(default_factory=list)
    cost: Decimal = Decimal("0")
    tokens: int = 0
    turns: int = 0
    state: str = "COMPLETED"  # COMPLETED | WAITING | FAILED | TIMEOUT | ERROR
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class CaseVerdict:
    case_id: str
    arm: str
    quality: Level
    citation: Level
    cost: Decimal
    coverage: dict[str, int | None]
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_id": self.case_id,
            "arm": self.arm,
            "quality": self.quality,
            "citation": self.citation,
            "cost": str(self.cost),
            "coverage": self.coverage,
            "reasons": self.reasons,
        }


def judge(case: Case, out: ArmOutput, store: TruthStore) -> CaseVerdict:
    reasons: list[str] = []
    version = store.data_version()
    if version != case.data_snapshot_id:
        return CaseVerdict(
            case.case_id,
            out.arm,
            "undecidable",
            "undecidable",
            out.cost,
            {},
            [f"dataset version {version} != case {case.data_snapshot_id}"],
        )
    if out.state != "COMPLETED":
        return CaseVerdict(
            case.case_id,
            out.arm,
            "undecidable",
            "undecidable",
            out.cost,
            {},
            [f"arm did not complete: {out.state} {out.error or ''}".strip()],
        )
    if not out.sqls:
        return CaseVerdict(
            case.case_id,
            out.arm,
            "undecidable",
            "fail",
            out.cost,
            {},
            ["no SQL produced"],
        )

    candidates: list[tuple[int, list[str], list[dict[str, Any]]] | None] = []
    for i, sql in enumerate(out.sqls):
        try:
            cols, rows = store.query(sql)
            candidates.append((i, cols, rows))
        except sqlite3.Error as exc:
            candidates.append(None)
            reasons.append(f"sql#{i} failed: {str(exc)[:120]}")

    coverage: dict[str, int | None] = {}
    for q in case.truth_queries:
        tcols, trows = store.truth(q)
        matched = None
        last_reason = ""
        for cand in candidates:
            if cand is None:
                continue
            i, ccols, crows = cand
            ok, why = same_result(ccols, crows, tcols, trows, q)
            if ok:
                matched = i
                break
            last_reason = why
        coverage[q.query_id] = matched
        if matched is None:
            reasons.append(f"{q.query_id} uncovered: {last_reason}")
    quality: Level = "pass" if all(v is not None for v in coverage.values()) else "fail"

    if not out.data_versions:
        citation: Level = "fail"
        reasons.append("no data_version cited")
    elif all(v == version for v in out.data_versions):
        citation = "pass"
    else:
        citation = "fail"
        reasons.append(f"cited versions {sorted(set(out.data_versions))} != {version}")
    return CaseVerdict(
        case.case_id, out.arm, quality, citation, out.cost, coverage, reasons
    )
