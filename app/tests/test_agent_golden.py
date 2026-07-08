from __future__ import annotations

from pathlib import Path

from scripts.agent_golden import load_golden_case, run_golden_case


def test_phase0_golden_case_is_deterministic() -> None:
    case = load_golden_case(Path("tests/golden/phase0_walking_skeleton.json"))

    result = run_golden_case(case)

    assert result.name == "phase0_walking_skeleton"
    assert result.llm_calls == 0
    assert result.timeline == case["expected_timeline"]


def test_first_m1_graph_golden_case_is_deterministic() -> None:
    case = load_golden_case(Path("tests/golden/first_m1_graph.json"))

    result = run_golden_case(case)

    assert result.name == "first_m1_graph"
    assert result.llm_calls == 0
    assert result.resumed_state == case["graph"]["expected_state"]
