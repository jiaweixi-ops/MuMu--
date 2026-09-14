from __future__ import annotations

from dataclasses import dataclass

from simcity_ai_mayor.city.map_model import CityMap, GridPoint
from simcity_ai_mayor.layout.optimizer import LayoutMove, LayoutOptimizer
from simcity_ai_mayor.layout.road_optimizer import RoadEdit, RoadTopologyOptimizer
from simcity_ai_mayor.layout.scorer import LayoutScore, LayoutScorer


@dataclass(frozen=True, slots=True)
class JointLayoutOptimizerConfig:
    max_rounds: int = 3
    min_improvement: float = 1e-6

    def __post_init__(self) -> None:
        if self.max_rounds <= 0:
            raise ValueError("max_rounds must be > 0")
        if self.min_improvement < 0:
            raise ValueError("min_improvement must be >= 0")


@dataclass(frozen=True, slots=True)
class JointLayoutPlan:
    before: LayoutScore
    after: LayoutScore
    moves: tuple[LayoutMove, ...]
    road_edits: tuple[RoadEdit, ...]
    resulting_map: CityMap

    @property
    def improvement(self) -> float:
        return self.after.total - self.before.total


class JointLayoutOptimizer:
    """Alternate building relocation and safe road-topology optimization."""

    def __init__(
        self,
        scorer: LayoutScorer,
        building_optimizer: LayoutOptimizer,
        road_optimizer: RoadTopologyOptimizer,
        *,
        config: JointLayoutOptimizerConfig | None = None,
    ) -> None:
        if building_optimizer.scorer is not scorer:
            raise ValueError("building_optimizer must share the joint scorer instance")
        if road_optimizer.scorer is not scorer:
            raise ValueError("road_optimizer must share the joint scorer instance")
        self.scorer = scorer
        self.building_optimizer = building_optimizer
        self.road_optimizer = road_optimizer
        self.config = config or JointLayoutOptimizerConfig()

    def optimize(self, city: CityMap) -> JointLayoutPlan:
        baseline_origins = {
            building.spec.building_id: building.origin for building in city.buildings
        }
        before = self.scorer.score(city, baseline_origins=baseline_origins)
        current = city
        current_score = before

        for _ in range(self.config.max_rounds):
            round_start = current_score.total

            building_plan = self.building_optimizer.optimize(current)
            if building_plan.after.total > current_score.total + self.config.min_improvement:
                current = building_plan.resulting_map
                current_score = self.scorer.score(
                    current,
                    baseline_origins=baseline_origins,
                )

            road_plan = self.road_optimizer.optimize(
                current,
                baseline_origins=baseline_origins,
            )
            if road_plan.after.total > current_score.total + self.config.min_improvement:
                current = road_plan.resulting_map
                current_score = road_plan.after

            if current_score.total <= round_start + self.config.min_improvement:
                break

        moves = tuple(
            LayoutMove(
                building.spec.building_id,
                baseline_origins[building.spec.building_id],
                building.origin,
            )
            for building in current.buildings
            if building.origin != baseline_origins[building.spec.building_id]
        )
        road_edits = RoadTopologyOptimizer._diff_roads(city, current)
        return JointLayoutPlan(before, current_score, moves, road_edits, current)
