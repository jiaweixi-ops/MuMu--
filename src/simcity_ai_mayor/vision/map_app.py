from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, UnidentifiedImageError

from simcity_ai_mayor.city.map_io import city_map_to_dict
from simcity_ai_mayor.city.map_model import BuildingKind, BuildingSpec, GridPoint
from simcity_ai_mayor.vision.map_detectors import (
    BuildingTemplateSpec,
    TemplateBlockedCellDetector,
    TemplateBuildingDetector,
    TemplatePatchSpec,
    TemplateRoadDetector,
)
from simcity_ai_mayor.vision.map_merge import CityMapMerger, ScannedMapTile
from simcity_ai_mayor.vision.map_scanner import (
    AffineGridCalibration,
    CalibratedCityMapScanner,
)


class MapScanConfigError(ValueError):
    pass


class _EmptyBlockedDetector:
    def detect(self, image, calibration):
        del image, calibration
        return ()


@dataclass(frozen=True, slots=True)
class TileConfig:
    image: Path
    grid_origin: GridPoint
    calibration: AffineGridCalibration


@dataclass(frozen=True, slots=True)
class MapScanConfig:
    width: int
    height: int
    tiles: tuple[TileConfig, ...]
    road_templates: tuple[TemplatePatchSpec, ...]
    blocked_templates: tuple[TemplatePatchSpec, ...]
    building_templates: tuple[BuildingTemplateSpec, ...]

    @classmethod
    def load(cls, path: str | Path) -> MapScanConfig:
        source = Path(path).expanduser().resolve()
        try:
            raw = json.loads(source.read_text(encoding="utf-8"))
        except OSError as exc:
            raise MapScanConfigError(f"cannot read map scan config: {source}") from exc
        except json.JSONDecodeError as exc:
            raise MapScanConfigError(f"invalid map scan JSON: {exc}") from exc
        if not isinstance(raw, dict):
            raise MapScanConfigError("map scan config root must be an object")
        base = source.parent
        global_size = _pair(raw.get("global_size"), "global_size")
        width, height = int(global_size[0]), int(global_size[1])
        if width <= 0 or height <= 0:
            raise MapScanConfigError("global_size values must be > 0")

        tiles = tuple(
            _tile(base, item) for item in _array(raw.get("tiles"), "tiles")
        )
        if not tiles:
            raise MapScanConfigError("at least one map tile is required")
        road_templates = tuple(
            _patch(base, item)
            for item in _array(raw.get("road_templates"), "road_templates")
        )
        if not road_templates:
            raise MapScanConfigError("at least one road template is required")
        blocked_templates = tuple(
            _patch(base, item)
            for item in _array(raw.get("blocked_templates", []), "blocked_templates")
        )
        building_templates = tuple(
            _building_template(base, item)
            for item in _array(raw.get("building_templates"), "building_templates")
        )
        if not building_templates:
            raise MapScanConfigError("at least one building template is required")
        return cls(
            width,
            height,
            tiles,
            road_templates,
            blocked_templates,
            building_templates,
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Scan calibrated SimCity map screenshots into a CityMap JSON"
    )
    parser.add_argument("--config", required=True)
    parser.add_argument("--output", required=True)
    return parser


def run_scan(config: MapScanConfig) -> dict[str, Any]:
    road_detector = TemplateRoadDetector(config.road_templates)
    building_detector = TemplateBuildingDetector(config.building_templates)
    blocked_detector = (
        TemplateBlockedCellDetector(config.blocked_templates)
        if config.blocked_templates
        else _EmptyBlockedDetector()
    )
    scanned_tiles: list[ScannedMapTile] = []
    for tile in config.tiles:
        image = _load_image(tile.image)
        scanner = CalibratedCityMapScanner(
            calibration=tile.calibration,
            road_detector=road_detector,
            building_detector=building_detector,
            blocked_detector=blocked_detector,
        )
        scanned_tiles.append(
            ScannedMapTile(tile.grid_origin, scanner.scan(image))
        )
    merged = CityMapMerger(config.width, config.height).merge(tuple(scanned_tiles))
    return city_map_to_dict(merged)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = MapScanConfig.load(args.config)
        result = run_scan(config)
        output = Path(args.output).expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"CityMap written to {output}")
        return 0
    except (MapScanConfigError, ValueError, OSError, UnidentifiedImageError) as exc:
        print(f"map scan failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


def _tile(base: Path, raw: Any) -> TileConfig:
    if not isinstance(raw, dict):
        raise MapScanConfigError("tile must be an object")
    image_value = raw.get("image")
    if not isinstance(image_value, str) or not image_value.strip():
        raise MapScanConfigError("tile.image is required")
    image = _resolve(base, image_value)
    origin_values = _pair(raw.get("grid_origin"), "tile.grid_origin")
    grid_origin = GridPoint(int(origin_values[0]), int(origin_values[1]))
    calibration_raw = raw.get("calibration")
    if not isinstance(calibration_raw, dict):
        raise MapScanConfigError("tile.calibration must be an object")
    local_size = _pair(calibration_raw.get("size"), "tile.calibration.size")
    origin_px = _pair(calibration_raw.get("origin_px"), "tile.calibration.origin_px")
    x_basis = _pair(calibration_raw.get("x_basis_px"), "tile.calibration.x_basis_px")
    y_basis = _pair(calibration_raw.get("y_basis_px"), "tile.calibration.y_basis_px")
    calibration = AffineGridCalibration(
        width_cells=int(local_size[0]),
        height_cells=int(local_size[1]),
        origin_x_px=float(origin_px[0]),
        origin_y_px=float(origin_px[1]),
        x_basis_x_px=float(x_basis[0]),
        x_basis_y_px=float(x_basis[1]),
        y_basis_x_px=float(y_basis[0]),
        y_basis_y_px=float(y_basis[1]),
    )
    return TileConfig(image, grid_origin, calibration)


def _patch(base: Path, raw: Any) -> TemplatePatchSpec:
    if not isinstance(raw, dict):
        raise MapScanConfigError("template entry must be an object")
    path_value = raw.get("template")
    if not isinstance(path_value, str) or not path_value.strip():
        raise MapScanConfigError("template path is required")
    offset = _pair(raw.get("offset_px", [0, 0]), "template.offset_px")
    threshold = float(raw.get("threshold", 0.92))
    return TemplatePatchSpec(
        _resolve(base, path_value),
        float(offset[0]),
        float(offset[1]),
        threshold,
    )


def _building_template(base: Path, raw: Any) -> BuildingTemplateSpec:
    if not isinstance(raw, dict):
        raise MapScanConfigError("building template must be an object")
    prototype_raw = raw.get("prototype")
    if not isinstance(prototype_raw, dict):
        raise MapScanConfigError("building template prototype must be an object")
    prototype_id = _required_string(prototype_raw, "id")
    try:
        kind = BuildingKind(_required_string(prototype_raw, "kind"))
    except ValueError as exc:
        raise MapScanConfigError(f"invalid building kind for {prototype_id!r}") from exc
    size = _pair(prototype_raw.get("size"), "building prototype size")
    prototype = BuildingSpec(
        building_id=prototype_id,
        kind=kind,
        width=int(size[0]),
        height=int(size[1]),
        population=int(prototype_raw.get("population", 0)),
        service_radius=int(prototype_raw.get("service_radius", 0)),
        beauty_radius=int(prototype_raw.get("beauty_radius", 0)),
        pollution=float(prototype_raw.get("pollution", 0.0)),
        traffic_load=float(prototype_raw.get("traffic_load", 0.0)),
        movable=bool(prototype_raw.get("movable", True)),
    )
    patch = _patch(base, raw)
    prefix_raw = raw.get("id_prefix")
    prefix = str(prefix_raw).strip() if prefix_raw else None
    return BuildingTemplateSpec(prototype, patch, prefix)


def _load_image(path: Path) -> Image.Image:
    with Image.open(path) as source:
        source.load()
        return source.convert("RGB")


def _resolve(base: Path, value: str) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _array(raw: Any, name: str) -> list[Any]:
    if not isinstance(raw, list):
        raise MapScanConfigError(f"{name} must be an array")
    return raw


def _pair(raw: Any, name: str) -> tuple[Any, Any]:
    values = _array(raw, name)
    if len(values) != 2:
        raise MapScanConfigError(f"{name} must contain exactly two values")
    return values[0], values[1]


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MapScanConfigError(f"{key} is required")
    return value.strip()


if __name__ == "__main__":
    raise SystemExit(main())
