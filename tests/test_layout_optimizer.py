import json

from simcity_ai_mayor.city.map_io import city_map_to_dict, load_city_map
from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    BuildingSpec,
    CityMap,
    GridPoint,
    PlacedBuilding,
)
from simcity_ai_mayor.layout.optimizer import LayoutOptimizer, LayoutOptimizerConfig
from simcity_ai_mayor.layout.scorer import LayoutMode, LayoutScorer, LayoutWeights


def sample_city() -> CityMap:
    roads = frozenset(GridPoint(x, 3) for x in range(8))
    buildings = (
        PlacedBuilding(
            BuildingSpec(
                "res-1",
                BuildingKind.RESIDENTIAL,
                1,
                1,
                population=1000,
                traffic_load=1.0,
            ),
            GridPoint(0, 2),
        ),
        PlacedBuilding(
            BuildingSpec(
                "res-2",
                BuildingKind.RESIDENTIAL,
                1,
                1,
                population=1000,
                traffic_load=1.0,
            ),
            GridPoint(1, 2),
        ),
        PlacedBuilding(
            BuildingSpec(
                "service-1",
                BuildingKind.SERVICE,
                1,
                1,
                service_radius=2,
                traffic_load=0.5,
            ),
            GridPoint(7, 2),
        ),
    )
    return CityMap(8, 6, roads=roads, buildings=buildings)


def test_city_map_rejects_overlap_and_can_move_building() -> None:
    city = sample_city()

    moved = city.move_building("service-1", GridPoint(2, 2))

    assert moved.building("service-1").origin == GridPoint(2, 2)
    assert moved.road_access(moved.building("service-1"))


def test_layout_optimizer_improves_service_coverage() -> None:
    city = sample_city()
    scorer = LayoutScorer(LayoutWeights.for_mode(LayoutMode.SERVICE))
    optimizer = LayoutOptimizer(
        scorer,
        config=LayoutOptimizerConfig(max_passes=3, candidate_limit_per_building=100),
    )

    plan = optimizer.optimize(city)

    assert plan.after.total > plan.before.total
    assert plan.after.service_coverage > plan.before.service_coverage
    assert any(move.building_id == "service-1" for move in plan.moves)


def test_city_map_json_round_trip(tmp_path) -> None:
    city = sample_city()
    path = tmp_path / "city.json"
    path.write_text(json.dumps(city_map_to_dict(city)), encoding="utf-8")

    loaded = load_city_map(path)

    assert loaded == city
