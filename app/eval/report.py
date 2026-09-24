"""四臂报告与发布门（F-EVAL-02、AT-27）：质量、引用、费用三列；更贵但只好一点点判负；不可判不折成通过。"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal

from .judge import CaseVerdict

Level = Literal["pass", "fail", "undecidable"]


@dataclass
class ArmSummary:
    arm: str
    n: int
    quality_pass: int
    citation_pass: int
    undecidable: int
    cost_total: Decimal

    @property
    def quality_rate(self) -> float:
        return self.quality_pass / self.n if self.n else 0.0

    @property
    def citation_rate(self) -> float:
        return self.citation_pass / self.n if self.n else 0.0

    @property
    def cost_mean(self) -> Decimal:
        return (self.cost_total / self.n) if self.n else Decimal("0")

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": self.arm,
            "n": self.n,
            "quality_pass": self.quality_pass,
            "quality_rate": round(self.quality_rate, 4),
            "citation_pass": self.citation_pass,
            "citation_rate": round(self.citation_rate, 4),
            "undecidable": self.undecidable,
            "cost_total": str(self.cost_total),
            "cost_mean": str(self.cost_mean.quantize(Decimal("0.0001"))),
        }


def summarize(verdicts: list[CaseVerdict]) -> dict[str, ArmSummary]:
    out: dict[str, ArmSummary] = {}
    for v in verdicts:
        s = out.setdefault(v.arm, ArmSummary(v.arm, 0, 0, 0, 0, Decimal("0")))
        s.n += 1
        s.quality_pass += v.quality == "pass"
        s.citation_pass += v.citation == "pass"
        s.undecidable += v.quality == "undecidable"
        s.cost_total += v.cost
    return out


@dataclass
class Gate:
    level: Level
    reason: str


def release_gate(
    baseline: ArmSummary,
    candidate: ArmSummary,
    *,
    min_gain: float = 0.10,
    max_undecidable_share: float = 0.10,
    cost_tolerance: Decimal = Decimal("1.0"),
    min_cases: int = 10,
) -> Gate:
    """专家包发布门。

    - 任一臂案例数少于 min_cases，或不可判占比超过阈值 → undecidable（不发布）；
    - 候选质量不高于基线 → fail；
    - 候选更贵（均价 > 基线均价 × cost_tolerance）且提升不足 min_gain → fail（AT-27）；
    - 否则 pass。
    """
    for s in (baseline, candidate):
        if s.n < min_cases:
            return Gate(
                "undecidable", f"arm {s.arm} has {s.n} cases, fewer than {min_cases}"
            )
        if s.undecidable / s.n > max_undecidable_share:
            return Gate(
                "undecidable",
                f"arm {s.arm}: {s.undecidable}/{s.n} undecidable",
            )
    gain = candidate.quality_rate - baseline.quality_rate
    dearer = candidate.cost_mean > baseline.cost_mean * cost_tolerance
    if gain <= 0:
        return Gate(
            "fail",
            f"quality {candidate.quality_rate:.2f} <= baseline {baseline.quality_rate:.2f}",
        )
    if dearer and gain < min_gain:
        return Gate(
            "fail",
            f"dearer ({candidate.cost_mean:.4f} vs {baseline.cost_mean:.4f}) with gain {gain:.2f} < {min_gain}",
        )
    return Gate(
        "pass",
        f"gain {gain:.2f}, cost {candidate.cost_mean:.4f} vs {baseline.cost_mean:.4f}",
    )


def render_markdown(
    title: str,
    verdicts: list[CaseVerdict],
    summaries: dict[str, ArmSummary],
    gate: Gate | None,
    meta: dict[str, Any],
) -> str:
    lines = [f"# {title}", ""]
    for k, v in meta.items():
        lines.append(f"- {k}: {v}")
    lines += [
        "",
        "## 各臂",
        "",
        "| 臂 | 案例 | 质量通过 | 引用通过 | 不可判 | 费用合计 | 费用均值 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summaries.values():
        lines.append(
            f"| {s.arm} | {s.n} | {s.quality_pass} ({s.quality_rate:.0%}) | {s.citation_pass} ({s.citation_rate:.0%}) | {s.undecidable} | {s.cost_total} | {s.cost_mean:.4f} |"
        )
    if gate is not None:
        lines += ["", f"## 发布门：**{gate.level}**", "", gate.reason]
    lines += [
        "",
        "## 逐案",
        "",
        "| 案例 | 臂 | 质量 | 引用 | 费用 | 原因 |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for v in verdicts:
        lines.append(
            f"| {v.case_id} | {v.arm} | {v.quality} | {v.citation} | {v.cost} | {'; '.join(v.reasons)[:200]} |"
        )
    return "\n".join(lines) + "\n"
