from dataclasses import replace
from pathlib import Path

from PIL import Image

from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    BuildingSpec,
    CityMap,
    GridPoint,
    PlacedBuilding,
)
from simcity_ai_mayor.vision.map_detectors import (
    BuildingTemplateSpec,
    TemplateBuildingDetector,
    TemplatePatchSpec,
    TemplateRoadDetector,
)
from simcity_ai_mayor.vision.map_merge import CityMapMerger, ScannedMapTile
from simcity_ai_mayor.vision.map_scanner import AffineGridCalibration


def patterned_template(path: Path) -> Image.Image:
    payload = bytes((x * 17 + y * 29) % 256 for y in range(10) for x in range(10))
    image = Image.frombytes("L", (10, 10), payload)
    image.save(path)
    return image


def calibration() -> AffineGridCalibration:
    return AffineGridCalibration(
        width_cells=3,
        height_cells=3,
        origin_x_px=20,
        origin_y_px=20,
        x_basis_x_px=20,
        x_basis_y_px=0,
        y_basis_x_px=0,
        y_basis_y_px=20,
    )


def test_affine_isometric_grid_round_trip() -> None:
    affine = AffineGridCalibration(
        width_cells=10,
        height_cells=10,
        origin_x_px=100,
        origin_y_px=50,
        x_basis_x_px=20,
        x_basis_y_px=10,
        y_basis_x_px=-20,
        y_basis_y_px=10,
    )
    point = GridPoint(3, 4)

    pixel = affine.grid_to_pixel(point)

    assert affine.pixel_to_grid(*pixel) == point


def test_template_road_detector_finds_calibrated_grid_cell(tmp_path) -> None:
    template_path = tmp_path / "road.png"
    template = patterned_template(template_path)
    frame = Image.new("L", (100, 100), 0)
    frame.paste(template, (35, 15))
    detector = TemplateRoadDetector(
        (TemplatePatchSpec(template_path, -5, -5, 0.99),)
    )

    roads = detector.detect(frame, calibration())

    assert roads == (GridPoint(1, 0),)


def test_template_building_detector_emits_non_overlapping_instance(tmp_path) -> None:
    template_path = tmp_path / "building.png"
    template = patterned_template(template_path)
    frame = Image.new("L", (100, 100), 0)
    frame.paste(template, (15, 35))
    prototype = BuildingSpec(
        "residential-prototype",
        BuildingKind.RESIDENTIAL,
        1,
        1,
        population=1000,
    )
    detector = TemplateBuildingDetector(
        (
            BuildingTemplateSpec(
                prototype,
                TemplatePatchSpec(template_path, -5, -5, 0.99),
                "residential",
            ),
        )
    )

    buildings = detector.detect(frame, calibration())

    assert len(buildings) == 1
    assert buildings[0].origin == GridPoint(0, 1)
    assert buildings[0].spec.building_id == "residential@0,1"


def test_map_merger_deduplicates_same_building_across_overlapping_tiles() -> None:
    prototype = BuildingSpec("res@1,1", BuildingKind.RESIDENTIAL, 1, 1)
    first = CityMap(
        3,
        3,
        buildings=(PlacedBuilding(prototype, GridPoint(1, 1)),),
    )
    second_spec = replace(prototype, building_id="res@0,1")
    second = CityMap(
        3,
        3,
        buildings=(PlacedBuilding(second_spec, GridPoint(0, 1)),),
    )

    merged = CityMapMerger(4, 3).merge(
        (
            ScannedMapTile(GridPoint(0, 0), first),
            ScannedMapTile(GridPoint(1, 0), second),
        )
    )

    assert len(merged.buildings) == 1
    assert merged.buildings[0].origin == GridPoint(1, 1)
    assert merged.buildings[0].spec.building_id == "res@1,1"
