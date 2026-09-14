from __future__ import annotations

from dataclasses import dataclass, replace
from enum import StrEnum
from math import hypot
from typing import Iterable


class BuildingKind(StrEnum):
    RESIDENTIAL = "RESIDENTIAL"
    COMMERCIAL = "COMMERCIAL"
    INDUSTRIAL = "INDUSTRIAL"
    SERVICE = "SERVICE"
    PARK = "PARK"
    SPECIAL = "SPECIAL"


@dataclass(frozen=True, slots=True, order=True)
class GridPoint:
    x: int
    y: int

    def neighbors4(self) -> tuple[GridPoint, ...]:
        return (
            GridPoint(self.x - 1, self.y),
            GridPoint(self.x + 1, self.y),
            GridPoint(self.x, self.y - 1),
            GridPoint(self.x, self.y + 1),
        )


@dataclass(frozen=True, slots=True)
class BuildingSpec:
    building_id: str
    kind: BuildingKind
    width: int
    height: int
    population: int = 0
    service_radius: int = 0
    beauty_radius: int = 0
    pollution: float = 0.0
    traffic_load: float = 0.0
    movable: bool = True

    def __post_init__(self) -> None:
        if not self.building_id.strip():
            raise ValueError("building_id is required")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("building width/height must be > 0")
        if self.population < 0:
            raise ValueError("population must be >= 0")
        if self.service_radius < 0 or self.beauty_radius < 0:
            raise ValueError("coverage radii must be >= 0")
        if self.pollution < 0 or self.traffic_load < 0:
            raise ValueError("pollution and traffic_load must be >= 0")


@dataclass(frozen=True, slots=True)
class PlacedBuilding:
    spec: BuildingSpec
    origin: GridPoint

    def footprint(self) -> frozenset[GridPoint]:
        return frozenset(
            GridPoint(self.origin.x + dx, self.origin.y + dy)
            for dx in range(self.spec.width)
            for dy in range(self.spec.height)
        )

    def center(self) -> tuple[float, float]:
        return (
            self.origin.x + (self.spec.width - 1) / 2,
            self.origin.y + (self.spec.height - 1) / 2,
        )


@dataclass(frozen=True, slots=True)
class CityMap:
    width: int
    height: int
    roads: frozenset[GridPoint] = frozenset()
    blocked: frozenset[GridPoint] = frozenset()
    buildings: tuple[PlacedBuilding, ...] = ()

    def __post_init__(self) -> None:
        if self.width <= 0 or self.height <= 0:
            raise ValueError("city width/height must be > 0")
        ids = [building.spec.building_id for building in self.buildings]
        if len(ids) != len(set(ids)):
            raise ValueError("building_id values must be unique")
        for point in (*self.roads, *self.blocked):
            if not self.in_bounds(point):
                raise ValueError(f"point out of bounds: {point}")
        occupied: set[GridPoint] = set()
        for building in self.buildings:
            for point in building.footprint():
                if not self.in_bounds(point):
                    raise ValueError(
                        f"building {building.spec.building_id!r} exceeds map bounds"
                    )
                if point in self.roads or point in self.blocked or point in occupied:
                    raise ValueError(
                        f"building {building.spec.building_id!r} overlaps occupied cell {point}"
                    )
                occupied.add(point)

    @property
    def area(self) -> int:
        return self.width * self.height

    def in_bounds(self, point: GridPoint) -> bool:
        return 0 <= point.x < self.width and 0 <= point.y < self.height

    def building(self, building_id: str) -> PlacedBuilding:
        for building in self.buildings:
            if building.spec.building_id == building_id:
                return building
        raise KeyError(building_id)

    def occupied_cells(self, *, exclude_building_id: str | None = None) -> frozenset[GridPoint]:
        cells: set[GridPoint] = set(self.roads) | set(self.blocked)
        for building in self.buildings:
            if building.spec.building_id == exclude_building_id:
                continue
            cells.update(building.footprint())
        return frozenset(cells)

    def road_access(self, building: PlacedBuilding) -> bool:
        for point in building.footprint():
            if any(neighbor in self.roads for neighbor in point.neighbors4()):
                return True
        return False

    def can_place(
        self,
        spec: BuildingSpec,
        origin: GridPoint,
        *,
        exclude_building_id: str | None = None,
        require_road_access: bool = True,
    ) -> bool:
        candidate = PlacedBuilding(spec, origin)
        footprint = candidate.footprint()
        if any(not self.in_bounds(point) for point in footprint):
            return False
        if footprint & self.occupied_cells(exclude_building_id=exclude_building_id):
            return False
        if require_road_access and not any(
            neighbor in self.roads
            for point in footprint
            for neighbor in point.neighbors4()
        ):
            return False
        return True

    def candidate_origins(
        self,
        spec: BuildingSpec,
        *,
        exclude_building_id: str | None = None,
        require_road_access: bool = True,
    ) -> tuple[GridPoint, ...]:
        candidates: list[GridPoint] = []
        for y in range(self.height - spec.height + 1):
            for x in range(self.width - spec.width + 1):
                point = GridPoint(x, y)
                if self.can_place(
                    spec,
                    point,
                    exclude_building_id=exclude_building_id,
                    require_road_access=require_road_access,
                ):
                    candidates.append(point)
        return tuple(candidates)

    def move_building(self, building_id: str, destination: GridPoint) -> CityMap:
        current = self.building(building_id)
        if not current.spec.movable:
            raise ValueError(f"building {building_id!r} is not movable")
        if not self.can_place(
            current.spec,
            destination,
            exclude_building_id=building_id,
            require_road_access=True,
        ):
            raise ValueError(f"invalid destination for {building_id!r}: {destination}")
        updated = tuple(
            replace(building, origin=destination)
            if building.spec.building_id == building_id
            else building
            for building in self.buildings
        )
        return replace(self, buildings=updated)

    def free_buildable_cells(self) -> frozenset[GridPoint]:
        occupied = self.occupied_cells()
        return frozenset(
            GridPoint(x, y)
            for y in range(self.height)
            for x in range(self.width)
            if GridPoint(x, y) not in occupied
        )

    def largest_free_component_ratio(self) -> float:
        remaining = set(self.free_buildable_cells())
        if not remaining:
            return 0.0
        largest = 0
        while remaining:
            start = remaining.pop()
            stack = [start]
            size = 1
            while stack:
                point = stack.pop()
                for neighbor in point.neighbors4():
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        stack.append(neighbor)
                        size += 1
            largest = max(largest, size)
        return largest / self.area

    def distance_between(self, a: PlacedBuilding, b: PlacedBuilding) -> float:
        ax, ay = a.center()
        bx, by = b.center()
        return hypot(ax - bx, ay - by)

    def with_buildings(self, buildings: Iterable[PlacedBuilding]) -> CityMap:
        return replace(self, buildings=tuple(buildings))
