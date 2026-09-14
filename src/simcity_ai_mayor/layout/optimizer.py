from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from simcity_ai_mayor.city.map_model import CityMap, GridPoint
from simcity_ai_mayor.layout.scorer import LayoutScore, LayoutScorer


@dataclass(frozen=True, slots=True)
class LayoutMove:
    building_id: str
    source: GridPoint
    destination: GridPoint


@dataclass(frozen=True, slots=True)
class LayoutPlan:
    before: LayoutScore
    after: LayoutScore
    moves: tuple[LayoutMove, ...]
    resulting_map: CityMap

    @property
    def improvement(self) -> float:
        return self.after.total - self.before.total


@dataclass(frozen=True, slots=True)
class LayoutOptimizerConfig:
    max_passes: int = 4
    candidate_limit_per_building: int = 200
    min_improvement: float = 1e-6

    def __post_init__(self) -> None:
        if self.max_passes <= 0:
            raise ValueError("max_passes must be > 0")
        if self.candidate_limit_per_building <= 0:
            raise ValueError("candidate_limit_per_building must be > 0")
        if self.min_improvement < 0:
            raise ValueError("min_improvement must be >= 0")


class LayoutOptimizer:
    """Deterministic coordinate-descent optimizer over movable building positions.

    This returns the best layout found within the configured candidate search. It does
    not claim a mathematical global optimum; road topology is held fixed in this stage.
    """

    def __init__(
        self,
        scorer: LayoutScorer,
        *,
        config: LayoutOptimizerConfig | None = None,
    ) -> None:
        self.scorer = scorer
        self.config = config or LayoutOptimizerConfig()

    def optimize(self, city: CityMap) -> LayoutPlan:
        baseline_origins = {
            building.spec.building_id: building.origin for building in city.buildings
        }
        before = self.scorer.score(city, baseline_origins=baseline_origins)
        current = city
        current_score = before

        for _ in range(self.config.max_passes):
            changed = False
            for building_id in self._movable_ids(current):
                current, current_score, moved = self._optimize_one(
                    current,
                    building_id,
                    current_score,
                    baseline_origins,
                )
                changed = changed or moved
            if not changed:
                break

        moves = tuple(
            LayoutMove(
                building_id=building.spec.building_id,
                source=baseline_origins[building.spec.building_id],
                destination=building.origin,
            )
            for building in current.buildings
            if building.origin != baseline_origins[building.spec.building_id]
        )
        return LayoutPlan(before=before, after=current_score, moves=moves, resulting_map=current)

    def _optimize_one(
        self,
        city: CityMap,
        building_id: str,
        incumbent_score: LayoutScore,
        baseline_origins: Mapping[str, GridPoint],
    ) -> tuple[CityMap, LayoutScore, bool]:
        building = city.building(building_id)
        candidates = city.candidate_origins(
            building.spec,
            exclude_building_id=building_id,
            require_road_access=True,
        )
        candidates = tuple(
            sorted(
                candidates,
                key=lambda point: (
                    abs(point.x - building.origin.x) + abs(point.y - building.origin.y),
                    point.y,
                    point.x,
                ),
            )[: self.config.candidate_limit_per_building]
        )

        best_city = city
        best_score = incumbent_score
        for candidate in candidates:
            if candidate == building.origin:
                continue
            trial = city.move_building(building_id, candidate)
            score = self.scorer.score(trial, baseline_origins=baseline_origins)
            if score.total > best_score.total + self.config.min_improvement:
                best_city = trial
                best_score = score

        return best_city, best_score, best_city is not city

    @staticmethod
    def _movable_ids(city: CityMap) -> tuple[str, ...]:
        return tuple(
            building.spec.building_id
            for building in city.buildings
            if building.spec.movable
        )
