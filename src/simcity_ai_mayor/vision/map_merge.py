from __future__ import annotations

from dataclasses import dataclass, replace

from simcity_ai_mayor.city.map_model import CityMap, GridPoint, PlacedBuilding


class MapMergeConflict(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ScannedMapTile:
    """One locally scanned viewport positioned in global logical-grid coordinates."""

    grid_origin: GridPoint
    city: CityMap


class CityMapMerger:
    def __init__(self, width: int, height: int) -> None:
        if width <= 0 or height <= 0:
            raise ValueError("merged city dimensions must be > 0")
        self.width = width
        self.height = height

    def merge(self, tiles: tuple[ScannedMapTile, ...]) -> CityMap:
        if not tiles:
            raise ValueError("at least one scanned tile is required")
        roads: set[GridPoint] = set()
        blocked: set[GridPoint] = set()
        accepted: list[PlacedBuilding] = []

        for tile in tiles:
            self._validate_tile_bounds(tile)
            roads.update(self._translate_points(tile.city.roads, tile.grid_origin))
            blocked.update(self._translate_points(tile.city.blocked, tile.grid_origin))
            for building in tile.city.buildings:
                translated = self._translate_building(building, tile.grid_origin)
                duplicate = self._find_duplicate(accepted, translated)
                if duplicate is not None:
                    continue
                conflict = self._find_overlap(accepted, translated)
                if conflict is not None:
                    raise MapMergeConflict(
                        f"overlapping building detections disagree: "
                        f"{conflict.spec.building_id!r} vs "
                        f"{translated.spec.building_id!r}"
                    )
                accepted.append(translated)

        try:
            return CityMap(
                self.width,
                self.height,
                roads=frozenset(roads),
                blocked=frozenset(blocked),
                buildings=tuple(accepted),
            )
        except ValueError as exc:
            raise MapMergeConflict(f"merged detector outputs are inconsistent: {exc}") from exc

    def _validate_tile_bounds(self, tile: ScannedMapTile) -> None:
        if tile.grid_origin.x < 0 or tile.grid_origin.y < 0:
            raise MapMergeConflict("tile origin must be inside global map")
        if tile.grid_origin.x + tile.city.width > self.width:
            raise MapMergeConflict("tile exceeds global map width")
        if tile.grid_origin.y + tile.city.height > self.height:
            raise MapMergeConflict("tile exceeds global map height")

    @staticmethod
    def _translate_points(
        points: frozenset[GridPoint],
        offset: GridPoint,
    ) -> set[GridPoint]:
        return {
            GridPoint(point.x + offset.x, point.y + offset.y)
            for point in points
        }

    @staticmethod
    def _translate_building(
        building: PlacedBuilding,
        offset: GridPoint,
    ) -> PlacedBuilding:
        origin = GridPoint(
            building.origin.x + offset.x,
            building.origin.y + offset.y,
        )
        prefix = building.spec.building_id.split("@", 1)[0]
        translated_spec = replace(
            building.spec,
            building_id=f"{prefix}@{origin.x},{origin.y}",
        )
        return PlacedBuilding(translated_spec, origin)

    @staticmethod
    def _find_duplicate(
        accepted: list[PlacedBuilding],
        candidate: PlacedBuilding,
    ) -> PlacedBuilding | None:
        candidate_prefix = candidate.spec.building_id.split("@", 1)[0]
        for current in accepted:
            current_prefix = current.spec.building_id.split("@", 1)[0]
            if (
                current_prefix == candidate_prefix
                and current.spec.kind is candidate.spec.kind
                and current.footprint() == candidate.footprint()
            ):
                return current
        return None

    @staticmethod
    def _find_overlap(
        accepted: list[PlacedBuilding],
        candidate: PlacedBuilding,
    ) -> PlacedBuilding | None:
        footprint = candidate.footprint()
        for current in accepted:
            if current.footprint() & footprint:
                return current
        return None
