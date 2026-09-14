from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from math import floor
from typing import Protocol

from PIL import Image

from simcity_ai_mayor.city.map_model import CityMap, GridPoint, PlacedBuilding


class GridCalibrationLike(Protocol):
    width_cells: int
    height_cells: int

    def pixel_to_grid(self, x: float, y: float) -> GridPoint: ...

    def grid_to_pixel(self, point: GridPoint) -> tuple[float, float]: ...


@dataclass(frozen=True, slots=True)
class GridCalibration:
    """Orthogonal calibration kept for flat/debug maps."""

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
        gx = floor((x - self.origin_x_px) / self.cell_width_px)
        gy = floor((y - self.origin_y_px) / self.cell_height_px)
        point = GridPoint(gx, gy)
        self._validate_point(point, (x, y))
        return point

    def grid_to_pixel(self, point: GridPoint) -> tuple[float, float]:
        self._validate_point(point, point)
        return (
            self.origin_x_px + (point.x + 0.5) * self.cell_width_px,
            self.origin_y_px + (point.y + 0.5) * self.cell_height_px,
        )

    def _validate_point(self, point: GridPoint, source: object) -> None:
        if not 0 <= point.x < self.width_cells or not 0 <= point.y < self.height_cells:
            raise ValueError(f"coordinate maps outside calibrated grid: {source}")


@dataclass(frozen=True, slots=True)
class AffineGridCalibration:
    """2-D affine lattice calibration for isometric/oblique city views.

    ``origin_*`` is the screen-space anchor for grid cell ``(0, 0)``. ``x_basis`` and
    ``y_basis`` are the screen-space vectors produced by moving one city-grid cell along
    each logical axis. They may point diagonally and are not required to be orthogonal.
    """

    width_cells: int
    height_cells: int
    origin_x_px: float
    origin_y_px: float
    x_basis_x_px: float
    x_basis_y_px: float
    y_basis_x_px: float
    y_basis_y_px: float

    def __post_init__(self) -> None:
        if self.width_cells <= 0 or self.height_cells <= 0:
            raise ValueError("grid dimensions must be > 0")
        determinant = self._determinant
        if abs(determinant) < 1e-9:
            raise ValueError("grid basis vectors must be linearly independent")

    @property
    def _determinant(self) -> float:
        return (
            self.x_basis_x_px * self.y_basis_y_px
            - self.x_basis_y_px * self.y_basis_x_px
        )

    def grid_to_pixel(self, point: GridPoint) -> tuple[float, float]:
        self._validate_point(point, point)
        return (
            self.origin_x_px
            + point.x * self.x_basis_x_px
            + point.y * self.y_basis_x_px,
            self.origin_y_px
            + point.x * self.x_basis_y_px
            + point.y * self.y_basis_y_px,
        )

    def pixel_to_grid(self, x: float, y: float) -> GridPoint:
        dx = x - self.origin_x_px
        dy = y - self.origin_y_px
        determinant = self._determinant
        gx_float = (dx * self.y_basis_y_px - dy * self.y_basis_x_px) / determinant
        gy_float = (self.x_basis_x_px * dy - self.x_basis_y_px * dx) / determinant
        point = GridPoint(round(gx_float), round(gy_float))
        self._validate_point(point, (x, y))
        return point

    def _validate_point(self, point: GridPoint, source: object) -> None:
        if not 0 <= point.x < self.width_cells or not 0 <= point.y < self.height_cells:
            raise ValueError(f"coordinate maps outside calibrated grid: {source}")


class RoadDetector(Protocol):
    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibrationLike,
    ) -> Iterable[GridPoint]: ...


class BuildingDetector(Protocol):
    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibrationLike,
    ) -> Iterable[PlacedBuilding]: ...


class BlockedCellDetector(Protocol):
    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibrationLike,
    ) -> Iterable[GridPoint]: ...


class CityMapScanner(Protocol):
    def scan(self, image: Image.Image) -> CityMap: ...


@dataclass(slots=True)
class CalibratedCityMapScanner:
    """Assemble a CityMap from calibrated detector outputs.

    The scanner intentionally does not invent game-specific templates. Phase -1b/V1
    must provide detectors calibrated on real SimCity screenshots.
    """

    calibration: GridCalibrationLike
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
