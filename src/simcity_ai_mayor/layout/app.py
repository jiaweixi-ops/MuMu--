from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from simcity_ai_mayor.city.map_io import CityMapFormatError, city_map_to_dict, load_city_map
from simcity_ai_mayor.layout.optimizer import LayoutOptimizer, LayoutOptimizerConfig
from simcity_ai_mayor.layout.scorer import LayoutMode, LayoutScorer, LayoutWeights


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Optimize a digital SimCity city map")
    parser.add_argument("--map", required=True, help="Path to city-map JSON")
    parser.add_argument(
        "--mode",
        choices=[mode.value for mode in LayoutMode],
        default=LayoutMode.BALANCED.value,
        help="Optimization objective preset",
    )
    parser.add_argument("--max-passes", type=int, default=4)
    parser.add_argument("--candidate-limit", type=int, default=200)
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    return parser


def run_layout(
    map_path: str | Path,
    *,
    mode: LayoutMode = LayoutMode.BALANCED,
    max_passes: int = 4,
    candidate_limit: int = 200,
) -> dict[str, object]:
    city = load_city_map(map_path)
    scorer = LayoutScorer(LayoutWeights.for_mode(mode))
    optimizer = LayoutOptimizer(
        scorer,
        config=LayoutOptimizerConfig(
            max_passes=max_passes,
            candidate_limit_per_building=candidate_limit,
        ),
    )
    plan = optimizer.optimize(city)
    return {
        "mode": mode.value,
        "before": asdict(plan.before),
        "after": asdict(plan.after),
        "improvement": plan.improvement,
        "moves": [
            {
                "building_id": move.building_id,
                "source": [move.source.x, move.source.y],
                "destination": [move.destination.x, move.destination.y],
            }
            for move in plan.moves
        ],
        "resulting_map": city_map_to_dict(plan.resulting_map),
        "scope": (
            "best layout found by deterministic building-relocation search; "
            "road topology is fixed and this is not a proof of global optimality"
        ),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_layout(
            args.map,
            mode=LayoutMode(args.mode),
            max_passes=args.max_passes,
            candidate_limit=args.candidate_limit,
        )
        encoded = json.dumps(result, ensure_ascii=False, indent=2)
        if args.output:
            Path(args.output).expanduser().resolve().write_text(encoded + "\n", encoding="utf-8")
        else:
            print(encoded)
        return 0
    except (CityMapFormatError, ValueError, OSError) as exc:
        print(f"layout optimization failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
