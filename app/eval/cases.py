"""冻结任务集（F-EVAL-01）：真值定在查询层。

两批：问数二十题（第 24 课格式）；财报体检十三题（`fin_review_13`，0008-info 段四），
后者多两样：真值可以按去重后的结果比；每案有可以机器检查的「必须提醒」。
"""

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
    distinct: bool = False  # 比对前对投影后的行去重：同一个事实在多处出现时用


@dataclass(frozen=True)
class CaveatCheck:
    """答案里必须出现（any_of 任一）或不得出现（none_of 任一）的说法。

    这是按关键词的近似检查，不是语义判断：通过不等于提醒写得对，
    不通过基本可以断定没有提醒。两项都给时都要满足。
    """

    check_id: str
    description: str
    any_of: tuple[str, ...] = ()
    none_of: tuple[str, ...] = ()

    def passes(self, text: str) -> bool:
        if self.any_of and not any(word in text for word in self.any_of):
            return False
        return not any(word in text for word in self.none_of)


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    question: str
    data_snapshot_id: str
    truth_queries: tuple[TruthQuery, ...]
    expected_intent: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    traps: tuple[str, ...] = ()
    required_caveats: tuple[str, ...] = ()
    caveat_checks: tuple[CaveatCheck, ...] = ()

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
                    distinct=bool(q.get("distinct", False)),
                )
                for q in d["truth_queries"]
            ),
            expected_intent=dict(d.get("expected_intent") or {}),
            tags=tuple(str(t) for t in d.get("tags", ())),
            traps=tuple(str(t) for t in d.get("traps", ())),
            required_caveats=tuple(str(t) for t in d.get("required_caveats", ())),
            caveat_checks=tuple(
                CaveatCheck(
                    check_id=str(c["check_id"]),
                    description=str(c.get("description", "")),
                    any_of=tuple(str(w) for w in c.get("any_of", ())),
                    none_of=tuple(str(w) for w in c.get("none_of", ())),
                )
                for c in d.get("caveat_checks", ())
            ),
        )


def load_cases(name: str = "data_query_20") -> list[Case]:
    raw = json.loads((CASES_DIR / f"{name}.json").read_text(encoding="utf-8"))
    return [Case.from_dict(d) for d in raw]
