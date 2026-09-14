from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Protocol

from PIL import Image

from simcity_ai_mayor.city.map_model import CityMap, GridPoint, PlacedBuilding


@dataclass(frozen=True, slots=True)
class GridCalibration:
    width_cells: int
    height_cells: int
    origin_x_px: float
    origin_y_px: float
    cell_width_px: float
    cell_height_px: float

    def __post_init__(self) -> None:
        if self.width_cells <= 0 or self.height_cells <= 0:
            raise ValueError("grid dimensions must be > 0")
        if self.cell_width_px <= 0 or self.cell_height_px <= 0:
            raise ValueError("grid cell pixel size must be > 0")

    def pixel_to_grid(self, x: float, y: float) -> GridPoint:
        gx = int((x - self.origin_x_px) // self.cell_width_px)
        gy = int((y - self.origin_y_px) // self.cell_height_px)
        point = GridPoint(gx, gy)
        if not 0 <= gx < self.width_cells or not 0 <= gy < self.height_cells:
            raise ValueError(f"pixel coordinate maps outside calibrated grid: {(x, y)}")
        return point


class RoadDetector(Protocol):
    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibration,
    ) -> Iterable[GridPoint]: ...


class BuildingDetector(Protocol):
    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibration,
    ) -> Iterable[PlacedBuilding]: ...


class BlockedCellDetector(Protocol):
    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibration,
    ) -> Iterable[GridPoint]: ...


class CityMapScanner(Protocol):
    def scan(self, image: Image.Image) -> CityMap: ...


@dataclass(slots=True)
class CalibratedCityMapScanner:
    """Assemble a CityMap from calibrated detector outputs.

    The scanner intentionally does not invent game-specific detectors. Phase -1b/V1
    must provide detectors trained or calibrated on real SimCity screenshots.
    """

    calibration: GridCalibration
    road_detector: RoadDetector
    building_detector: BuildingDetector
    blocked_detector: BlockedCellDetector

    def scan(self, image: Image.Image) -> CityMap:
        roads = frozenset(self.road_detector.detect(image, self.calibration))
        buildings = tuple(self.building_detector.detect(image, self.calibration))
        blocked = frozenset(self.blocked_detector.detect(image, self.calibration))
        return CityMap(
            width=self.calibration.width_cells,
            height=self.calibration.height_cells,
            roads=roads,
            blocked=blocked,
            buildings=buildings,
        )
