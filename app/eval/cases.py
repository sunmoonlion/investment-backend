"""冻结任务集（F-EVAL-01）：真值定在查询层。第一批是问数二十题（第 24 课格式）。"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

CASES_DIR = Path(__file__).resolve().parent / "cases"


@dataclass(frozen=True)
class TruthQuery:
    query_id: str
    sql: str
    expected_columns: tuple[str, ...]
    key_columns: tuple[str, ...]
    value_columns: tuple[str, ...]
    tolerance: float
    purpose: str = ""


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    question: str
    data_snapshot_id: str
    truth_queries: tuple[TruthQuery, ...]
    expected_intent: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Case:
        return cls(
            case_id=str(d["case_id"]),
            category=str(d.get("category", "")),
            question=str(d["question"]),
            data_snapshot_id=str(d["data_snapshot_id"]),
            truth_queries=tuple(
                TruthQuery(
                    query_id=str(q["query_id"]),
                    sql=str(q["sql"]),
                    expected_columns=tuple(str(c) for c in q["expected_columns"]),
                    key_columns=tuple(str(c) for c in q.get("key_columns", ())),
                    value_columns=tuple(str(c) for c in q.get("value_columns", ())),
                    tolerance=float(q.get("tolerance", 0.0)),
                    purpose=str(q.get("purpose", "")),
                )
                for q in d["truth_queries"]
            ),
            expected_intent=dict(d.get("expected_intent") or {}),
            tags=tuple(str(t) for t in d.get("tags", ())),
        )


def load_cases(name: str = "data_query_20") -> list[Case]:
    raw = json.loads((CASES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return [Case.from_dict(d) for d in raw]
