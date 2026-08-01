from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.agent_golden import load_golden_case, run_golden_case

DEFAULT_FIXTURE = Path("tests/golden/phase0_walking_skeleton.json")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run deterministic agent golden cases.")
    parser.add_argument(
        "--fixture",
        type=Path,
        default=DEFAULT_FIXTURE,
        help="Path to a golden-case JSON fixture.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    case = load_golden_case(args.fixture)
    result = run_golden_case(case)
    print(
        json.dumps(
            {
                "golden_case": result.name,
                "status": "ok",
                "timeline": result.timeline,
                "resumed_state": result.resumed_state,
                "llm_calls": result.llm_calls,
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
