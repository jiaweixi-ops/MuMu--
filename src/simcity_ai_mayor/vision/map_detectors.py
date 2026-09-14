from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

from PIL import Image

from simcity_ai_mayor.city.map_model import BuildingSpec, GridPoint, PlacedBuilding
from simcity_ai_mayor.vision.map_scanner import GridCalibrationLike
from simcity_ai_mayor.vision.similarity import NccTemplateProfile


class MapDetectorConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class TemplatePatchSpec:
    template_path: Path
    offset_x_px: float
    offset_y_px: float
    threshold: float = 0.92

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise MapDetectorConfigError("template threshold must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class BuildingTemplateSpec:
    prototype: BuildingSpec
    patch: TemplatePatchSpec
    id_prefix: str | None = None


@dataclass(frozen=True, slots=True)
class _Detection:
    score: float
    building: PlacedBuilding


class _LoadedPatch:
    def __init__(self, spec: TemplatePatchSpec) -> None:
        self.spec = spec
        path = spec.template_path.expanduser().resolve()
        if not path.exists():
            raise MapDetectorConfigError(f"template does not exist: {path}")
        with Image.open(path) as source:
            template = source.convert("L")
            template.load()
        if template.width <= 0 or template.height <= 0:
            raise MapDetectorConfigError(f"template has invalid size: {path}")
        self._profile = NccTemplateProfile.from_image(template)

    def score_at(
        self,
        image: Image.Image,
        calibration: GridCalibrationLike,
        point: GridPoint,
    ) -> float | None:
        anchor_x, anchor_y = calibration.grid_to_pixel(point)
        left = round(anchor_x + self.spec.offset_x_px)
        top = round(anchor_y + self.spec.offset_y_px)
        right = left + self._profile.size[0]
        bottom = top + self._profile.size[1]
        if left < 0 or top < 0 or right > image.width or bottom > image.height:
            return None
        sample = image.crop((left, top, right, bottom))
        return self._profile.score(sample)

    def matches(
        self,
        image: Image.Image,
        calibration: GridCalibrationLike,
        point: GridPoint,
    ) -> tuple[bool, float]:
        score = self.score_at(image, calibration, point)
        if score is None:
            return False, 0.0
        return score >= self.spec.threshold, score


class _TemplateCellDetector:
    def __init__(self, templates: tuple[TemplatePatchSpec, ...]) -> None:
        if not templates:
            raise MapDetectorConfigError("at least one cell template is required")
        self._templates = tuple(_LoadedPatch(spec) for spec in templates)

    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibrationLike,
    ) -> tuple[GridPoint, ...]:
        detected: list[GridPoint] = []
        for y in range(calibration.height_cells):
            for x in range(calibration.width_cells):
                point = GridPoint(x, y)
                if any(
                    template.matches(image, calibration, point)[0]
                    for template in self._templates
                ):
                    detected.append(point)
        return tuple(detected)


class TemplateRoadDetector(_TemplateCellDetector):
    """Detect road grid cells using one or more calibrated road-orientation templates."""


class TemplateBlockedCellDetector(_TemplateCellDetector):
    """Detect non-buildable grid cells using calibrated obstacle templates."""


class TemplateBuildingDetector:
    """Scan calibrated grid origins for known building templates.

    Candidate detections are sorted by NCC score and greedily de-duplicated by logical
    footprint. The generated instance ID is deterministic for a snapshot and includes
    the detected grid origin; persistent cross-frame identity is a separate tracking
    concern and is intentionally not guessed here.
    """

    def __init__(self, templates: tuple[BuildingTemplateSpec, ...]) -> None:
        if not templates:
            raise MapDetectorConfigError("at least one building template is required")
        prefixes = [item.id_prefix or item.prototype.building_id for item in templates]
        if len(prefixes) != len(set(prefixes)):
            raise MapDetectorConfigError("building template id prefixes must be unique")
        self._templates = tuple(
            (spec, _LoadedPatch(spec.patch)) for spec in templates
        )

    def detect(
        self,
        image: Image.Image,
        calibration: GridCalibrationLike,
    ) -> tuple[PlacedBuilding, ...]:
        candidates: list[_Detection] = []
        for spec, patch in self._templates:
            prototype = spec.prototype
            max_x = calibration.width_cells - prototype.width
            max_y = calibration.height_cells - prototype.height
            for y in range(max_y + 1):
                for x in range(max_x + 1):
                    origin = GridPoint(x, y)
                    matched, score = patch.matches(image, calibration, origin)
                    if not matched:
                        continue
                    prefix = spec.id_prefix or prototype.building_id
                    instance_spec = replace(
                        prototype,
                        building_id=f"{prefix}@{x},{y}",
                    )
                    candidates.append(
                        _Detection(score, PlacedBuilding(instance_spec, origin))
                    )

        candidates.sort(
            key=lambda item: (
                -item.score,
                item.building.origin.y,
                item.building.origin.x,
                item.building.spec.building_id,
            )
        )
        accepted: list[PlacedBuilding] = []
        occupied: set[GridPoint] = set()
        for candidate in candidates:
            footprint = candidate.building.footprint()
            if footprint & occupied:
                continue
            accepted.append(candidate.building)
            occupied.update(footprint)
        return tuple(accepted)
