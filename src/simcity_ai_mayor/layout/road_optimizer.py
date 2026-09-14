from __future__ import annotations

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, replace
from enum import StrEnum

from simcity_ai_mayor.city.map_model import CityMap, GridPoint, PlacedBuilding
from simcity_ai_mayor.layout.scorer import LayoutScore, LayoutScorer


class RoadEditKind(StrEnum):
    BUILD = "BUILD"
    REMOVE = "REMOVE"


@dataclass(frozen=True, slots=True)
class RoadEdit:
    kind: RoadEditKind
    point: GridPoint


@dataclass(frozen=True, slots=True)
class RoadOptimizationPlan:
    before: LayoutScore
    after: LayoutScore
    edits: tuple[RoadEdit, ...]
    resulting_map: CityMap

    @property
    def improvement(self) -> float:
        return self.after.total - self.before.total


@dataclass(frozen=True, slots=True)
class RoadOptimizerConfig:
    max_remove_passes: int = 3
    max_connector_length: int = 24
    min_improvement: float = 1e-6

    def __post_init__(self) -> None:
        if self.max_remove_passes <= 0:
            raise ValueError("max_remove_passes must be > 0")
        if self.max_connector_length <= 0:
            raise ValueError("max_connector_length must be > 0")
        if self.min_improvement < 0:
            raise ValueError("min_improvement must be >= 0")


class RoadTopologyOptimizer:
    """Repair road access and remove provably redundant road cells.

    New roads are shortest free-cell connectors from an unserved building to the existing
    road network. Removal is accepted only when the road graph stays connected, every
    building keeps road access, and the layout score improves.
    """

    def __init__(
        self,
        scorer: LayoutScorer,
        *,
        config: RoadOptimizerConfig | None = None,
    ) -> None:
        self.scorer = scorer
        self.config = config or RoadOptimizerConfig()

    def optimize(
        self,
        city: CityMap,
        *,
        baseline_origins: Mapping[str, GridPoint] | None = None,
    ) -> RoadOptimizationPlan:
        before = self.scorer.score(city, baseline_origins=baseline_origins)
        current = city

        current = self._repair_missing_access(current)
        current_score = self.scorer.score(current, baseline_origins=baseline_origins)

        for _ in range(self.config.max_remove_passes):
            changed = False
            for point in sorted(current.roads):
                trial = self._try_remove(current, point)
                if trial is None:
                    continue
                score = self.scorer.score(trial, baseline_origins=baseline_origins)
                if score.total > current_score.total + self.config.min_improvement:
                    current = trial
                    current_score = score
                    changed = True
            if not changed:
                break

        edits = self._diff_roads(city, current)
        return RoadOptimizationPlan(before, current_score, edits, current)

    def _repair_missing_access(self, city: CityMap) -> CityMap:
        current = city
        for building in current.buildings:
            if current.road_access(building):
                continue
            path = self._shortest_connector(current, building)
            if path is None:
                continue
            current = replace(current, roads=frozenset((*current.roads, *path)))
        return current

    def _shortest_connector(
        self,
        city: CityMap,
        building: PlacedBuilding,
    ) -> tuple[GridPoint, ...] | None:
        if not city.roads:
            return None
        occupied = city.occupied_cells(exclude_building_id=building.spec.building_id)
        footprint = building.footprint()
        starts = {
            neighbor
            for point in footprint
            for neighbor in point.neighbors4()
            if city.in_bounds(neighbor)
            and neighbor not in occupied
            and neighbor not in footprint
        }
        if not starts:
            return None

        queue: deque[tuple[GridPoint, tuple[GridPoint, ...]]] = deque(
            (point, (point,)) for point in sorted(starts)
        )
        visited = set(starts)
        while queue:
            point, path = queue.popleft()
            if len(path) > self.config.max_connector_length:
                continue
            if any(neighbor in city.roads for neighbor in point.neighbors4()):
                return path
            for neighbor in point.neighbors4():
                if (
                    not city.in_bounds(neighbor)
                    or neighbor in visited
                    or neighbor in occupied
                    or neighbor in footprint
                    or neighbor in city.roads
                ):
                    continue
                visited.add(neighbor)
                queue.append((neighbor, (*path, neighbor)))
        return None

    def _try_remove(self, city: CityMap, point: GridPoint) -> CityMap | None:
        roads = frozenset(road for road in city.roads if road != point)
        if not roads:
            return None
        trial = replace(city, roads=roads)
        if not self._roads_connected(trial.roads):
            return None
        if any(not trial.road_access(building) for building in trial.buildings):
            return None
        return trial

    @staticmethod
    def _roads_connected(roads: frozenset[GridPoint]) -> bool:
        if not roads:
            return True
        start = next(iter(roads))
        visited = {start}
        stack = [start]
        while stack:
            point = stack.pop()
            for neighbor in point.neighbors4():
                if neighbor in roads and neighbor not in visited:
                    visited.add(neighbor)
                    stack.append(neighbor)
        return len(visited) == len(roads)

    @staticmethod
    def _diff_roads(before: CityMap, after: CityMap) -> tuple[RoadEdit, ...]:
        built = tuple(
            RoadEdit(RoadEditKind.BUILD, point)
            for point in sorted(after.roads - before.roads)
        )
        removed = tuple(
            RoadEdit(RoadEditKind.REMOVE, point)
            for point in sorted(before.roads - after.roads)
        )
        return (*built, *removed)
