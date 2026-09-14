from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

from simcity_ai_mayor.city.map_io import CityMapFormatError, city_map_to_dict, load_city_map
from simcity_ai_mayor.layout.construction import ConstructionPlanner
from simcity_ai_mayor.layout.joint_optimizer import (
    JointLayoutOptimizer,
    JointLayoutOptimizerConfig,
)
from simcity_ai_mayor.layout.optimizer import LayoutOptimizer, LayoutOptimizerConfig
from simcity_ai_mayor.layout.road_optimizer import RoadOptimizerConfig, RoadTopologyOptimizer
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
    parser.add_argument("--joint-rounds", type=int, default=3)
    parser.add_argument("--road-remove-passes", type=int, default=3)
    parser.add_argument("--road-connector-length", type=int, default=24)
    parser.add_argument("--output", default=None, help="Optional JSON output path")
    return parser


def run_layout(
    map_path: str | Path,
    *,
    mode: LayoutMode = LayoutMode.BALANCED,
    max_passes: int = 4,
    candidate_limit: int = 200,
    joint_rounds: int = 3,
    road_remove_passes: int = 3,
    road_connector_length: int = 24,
) -> dict[str, object]:
    city = load_city_map(map_path)
    scorer = LayoutScorer(LayoutWeights.for_mode(mode))
    building_optimizer = LayoutOptimizer(
        scorer,
        config=LayoutOptimizerConfig(
            max_passes=max_passes,
            candidate_limit_per_building=candidate_limit,
        ),
    )
    road_optimizer = RoadTopologyOptimizer(
        scorer,
        config=RoadOptimizerConfig(
            max_remove_passes=road_remove_passes,
            max_connector_length=road_connector_length,
        ),
    )
    optimizer = JointLayoutOptimizer(
        scorer,
        building_optimizer,
        road_optimizer,
        config=JointLayoutOptimizerConfig(max_rounds=joint_rounds),
    )
    plan = optimizer.optimize(city)
    construction = ConstructionPlanner().build(city, plan)
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
        "road_edits": [
            {
                "kind": edit.kind.value,
                "point": [edit.point.x, edit.point.y],
            }
            for edit in plan.road_edits
        ],
        "construction": {
            "requires_staging": construction.requires_staging,
            "ordered_steps": [_step_to_dict(step) for step in construction.ordered_steps],
            "unresolved_steps": [
                _step_to_dict(step) for step in construction.unresolved_steps
            ],
        },
        "resulting_map": city_map_to_dict(plan.resulting_map),
        "scope": (
            "best layout found by deterministic building-relocation plus safe road-topology "
            "search; this is not a proof of mathematical global optimality"
        ),
    }


def _step_to_dict(step) -> dict[str, object]:
    return {
        "step_id": step.step_id,
        "kind": step.kind.value,
        "building_id": step.building_id,
        "source": None if step.source is None else [step.source.x, step.source.y],
        "destination": (
            None if step.destination is None else [step.destination.x, step.destination.y]
        ),
        "point": None if step.point is None else [step.point.x, step.point.y],
        "depends_on": list(step.depends_on),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = run_layout(
            args.map,
            mode=LayoutMode(args.mode),
            max_passes=args.max_passes,
            candidate_limit=args.candidate_limit,
            joint_rounds=args.joint_rounds,
            road_remove_passes=args.road_remove_passes,
            road_connector_length=args.road_connector_length,
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
