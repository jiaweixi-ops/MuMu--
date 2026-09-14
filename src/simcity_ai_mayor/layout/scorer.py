from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import hypot
from statistics import fmean

from simcity_ai_mayor.city.map_model import BuildingKind, CityMap, GridPoint, PlacedBuilding


class LayoutMode(StrEnum):
    BALANCED = "BALANCED"
    POPULATION = "POPULATION"
    SERVICE = "SERVICE"
    BEAUTY = "BEAUTY"
    EXPANSION = "EXPANSION"


@dataclass(frozen=True, slots=True)
class LayoutWeights:
    road_access: float = 0.16
    service_coverage: float = 0.20
    beauty_coverage: float = 0.10
    pollution_separation: float = 0.14
    traffic_efficiency: float = 0.12
    zone_compactness: float = 0.10
    expansion_space: float = 0.12
    road_efficiency: float = 0.06
    move_penalty: float = 0.08

    def __post_init__(self) -> None:
        values = (
            self.road_access,
            self.service_coverage,
            self.beauty_coverage,
            self.pollution_separation,
            self.traffic_efficiency,
            self.zone_compactness,
            self.expansion_space,
            self.road_efficiency,
            self.move_penalty,
        )
        if any(value < 0 for value in values):
            raise ValueError("layout weights must be >= 0")
        if sum(values[:-1]) <= 0:
            raise ValueError("at least one positive layout reward weight is required")

    @classmethod
    def for_mode(cls, mode: LayoutMode) -> LayoutWeights:
        if mode is LayoutMode.POPULATION:
            return cls(
                road_access=0.20,
                service_coverage=0.22,
                beauty_coverage=0.04,
                pollution_separation=0.12,
                traffic_efficiency=0.12,
                zone_compactness=0.12,
                expansion_space=0.14,
                road_efficiency=0.04,
                move_penalty=0.06,
            )
        if mode is LayoutMode.SERVICE:
            return cls(
                road_access=0.16,
                service_coverage=0.34,
                beauty_coverage=0.08,
                pollution_separation=0.12,
                traffic_efficiency=0.10,
                zone_compactness=0.08,
                expansion_space=0.08,
                road_efficiency=0.04,
                move_penalty=0.08,
            )
        if mode is LayoutMode.BEAUTY:
            return cls(
                road_access=0.12,
                service_coverage=0.14,
                beauty_coverage=0.28,
                pollution_separation=0.14,
                traffic_efficiency=0.08,
                zone_compactness=0.10,
                expansion_space=0.08,
                road_efficiency=0.06,
                move_penalty=0.10,
            )
        if mode is LayoutMode.EXPANSION:
            return cls(
                road_access=0.14,
                service_coverage=0.14,
                beauty_coverage=0.05,
                pollution_separation=0.12,
                traffic_efficiency=0.10,
                zone_compactness=0.10,
                expansion_space=0.28,
                road_efficiency=0.07,
                move_penalty=0.06,
            )
        return cls()


@dataclass(frozen=True, slots=True)
class LayoutScore:
    total: float
    road_access: float
    service_coverage: float
    beauty_coverage: float
    pollution_separation: float
    traffic_efficiency: float
    zone_compactness: float
    expansion_space: float
    road_efficiency: float
    move_penalty: float


class LayoutScorer:
    def __init__(self, weights: LayoutWeights | None = None) -> None:
        self.weights = weights or LayoutWeights()

    def score(
        self,
        city: CityMap,
        *,
        baseline_origins: Mapping[str, GridPoint] | None = None,
    ) -> LayoutScore:
        road_access = self._road_access(city)
        service_coverage = self._coverage(city, BuildingKind.SERVICE)
        beauty_coverage = self._coverage(city, BuildingKind.PARK)
        pollution_separation = self._pollution_separation(city)
        traffic_efficiency = self._traffic_efficiency(city)
        zone_compactness = self._zone_compactness(city)
        expansion_space = self._expansion_space(city)
        road_efficiency = max(0.0, 1.0 - len(city.roads) / city.area)
        move_penalty = self._move_penalty(city, baseline_origins)

        rewards = (
            road_access * self.weights.road_access
            + service_coverage * self.weights.service_coverage
            + beauty_coverage * self.weights.beauty_coverage
            + pollution_separation * self.weights.pollution_separation
            + traffic_efficiency * self.weights.traffic_efficiency
            + zone_compactness * self.weights.zone_compactness
            + expansion_space * self.weights.expansion_space
            + road_efficiency * self.weights.road_efficiency
        )
        reward_weight = (
            self.weights.road_access
            + self.weights.service_coverage
            + self.weights.beauty_coverage
            + self.weights.pollution_separation
            + self.weights.traffic_efficiency
            + self.weights.zone_compactness
            + self.weights.expansion_space
            + self.weights.road_efficiency
        )
        total = rewards / reward_weight - move_penalty * self.weights.move_penalty
        return LayoutScore(
            total=total,
            road_access=road_access,
            service_coverage=service_coverage,
            beauty_coverage=beauty_coverage,
            pollution_separation=pollution_separation,
            traffic_efficiency=traffic_efficiency,
            zone_compactness=zone_compactness,
            expansion_space=expansion_space,
            road_efficiency=road_efficiency,
            move_penalty=move_penalty,
        )

    @staticmethod
    def _residential(city: CityMap) -> tuple[PlacedBuilding, ...]:
        return tuple(
            building
            for building in city.buildings
            if building.spec.kind is BuildingKind.RESIDENTIAL
        )

    def _road_access(self, city: CityMap) -> float:
        if not city.buildings:
            return 1.0
        weights: list[float] = []
        values: list[float] = []
        for building in city.buildings:
            weight = max(1.0, float(building.spec.population), building.spec.traffic_load)
            weights.append(weight)
            values.append(1.0 if city.road_access(building) else 0.0)
        return self._weighted_mean(values, weights)

    def _coverage(self, city: CityMap, source_kind: BuildingKind) -> float:
        residential = self._residential(city)
        if not residential:
            return 1.0
        sources = tuple(
            building for building in city.buildings if building.spec.kind is source_kind
        )
        if not sources:
            return 0.0

        values: list[float] = []
        weights: list[float] = []
        for target in residential:
            tx, ty = target.center()
            covered = False
            for source in sources:
                radius = (
                    source.spec.service_radius
                    if source_kind is BuildingKind.SERVICE
                    else source.spec.beauty_radius
                )
                if radius <= 0:
                    continue
                sx, sy = source.center()
                if hypot(tx - sx, ty - sy) <= radius:
                    covered = True
                    break
            values.append(1.0 if covered else 0.0)
            weights.append(max(1.0, float(target.spec.population)))
        return self._weighted_mean(values, weights)

    def _pollution_separation(self, city: CityMap) -> float:
        residential = self._residential(city)
        polluters = tuple(
            building for building in city.buildings if building.spec.pollution > 0
        )
        if not residential or not polluters:
            return 1.0
        diagonal = max(1.0, hypot(city.width - 1, city.height - 1))
        values: list[float] = []
        weights: list[float] = []
        for target in residential:
            nearest = min(city.distance_between(target, polluter) for polluter in polluters)
            values.append(min(1.0, nearest / diagonal))
            weights.append(max(1.0, float(target.spec.population)))
        return self._weighted_mean(values, weights)

    def _traffic_efficiency(self, city: CityMap) -> float:
        traffic_buildings = tuple(
            building for building in city.buildings if building.spec.traffic_load > 0
        )
        if not traffic_buildings:
            return 1.0
        values: list[float] = []
        weights: list[float] = []
        for building in traffic_buildings:
            distance = self._distance_to_nearest_road(city, building)
            values.append(1.0 / (1.0 + distance))
            weights.append(building.spec.traffic_load)
        return self._weighted_mean(values, weights)

    def _zone_compactness(self, city: CityMap) -> float:
        diagonal = max(1.0, hypot(city.width - 1, city.height - 1))
        category_scores: list[float] = []
        for kind in (
            BuildingKind.RESIDENTIAL,
            BuildingKind.COMMERCIAL,
            BuildingKind.INDUSTRIAL,
        ):
            buildings = [building for building in city.buildings if building.spec.kind is kind]
            if len(buildings) < 2:
                continue
            distances = [
                city.distance_between(buildings[index], buildings[other]) / diagonal
                for index in range(len(buildings))
                for other in range(index + 1, len(buildings))
            ]
            category_scores.append(max(0.0, 1.0 - fmean(distances)))
        return fmean(category_scores) if category_scores else 1.0

    @staticmethod
    def _expansion_space(city: CityMap) -> float:
        free_count = len(city.free_buildable_cells())
        if free_count == 0:
            return 0.0
        largest_ratio = city.largest_free_component_ratio()
        free_ratio = free_count / city.area
        return min(1.0, largest_ratio / free_ratio)

    @staticmethod
    def _move_penalty(
        city: CityMap,
        baseline_origins: Mapping[str, GridPoint] | None,
    ) -> float:
        if not baseline_origins or not city.buildings:
            return 0.0
        normalizer = max(1, city.width + city.height - 2)
        distances: list[float] = []
        for building in city.buildings:
            original = baseline_origins.get(building.spec.building_id)
            if original is None:
                continue
            distance = abs(building.origin.x - original.x) + abs(building.origin.y - original.y)
            distances.append(distance / normalizer)
        return fmean(distances) if distances else 0.0

    @staticmethod
    def _distance_to_nearest_road(city: CityMap, building: PlacedBuilding) -> int:
        if not city.roads:
            return city.width + city.height
        return min(
            abs(point.x - road.x) + abs(point.y - road.y)
            for point in building.footprint()
            for road in city.roads
        )

    @staticmethod
    def _weighted_mean(values: list[float], weights: list[float]) -> float:
        total_weight = sum(weights)
        if total_weight <= 0:
            return 0.0
        weighted_sum = sum(
            value * weight
            for value, weight in zip(values, weights, strict=True)
        )
        return weighted_sum / total_weight
