from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    BuildingSpec,
    CityMap,
    GridPoint,
    PlacedBuilding,
)
from simcity_ai_mayor.layout.joint_optimizer import JointLayoutOptimizer
from simcity_ai_mayor.layout.optimizer import LayoutOptimizer, LayoutOptimizerConfig
from simcity_ai_mayor.layout.road_optimizer import (
    RoadEditKind,
    RoadOptimizerConfig,
    RoadTopologyOptimizer,
)
from simcity_ai_mayor.layout.scorer import LayoutScorer


def building(building_id: str, x: int, y: int) -> PlacedBuilding:
    return PlacedBuilding(
        BuildingSpec(
            building_id,
            BuildingKind.RESIDENTIAL,
            1,
            1,
            population=1000,
            traffic_load=1.0,
        ),
        GridPoint(x, y),
    )


def test_road_optimizer_removes_redundant_road_without_losing_access() -> None:
    roads = frozenset(
        {
            GridPoint(1, 1),
            GridPoint(2, 1),
            GridPoint(2, 2),
            GridPoint(1, 2),
        }
    )
    city = CityMap(5, 5, roads=roads, buildings=(building("res", 0, 1),))
    scorer = LayoutScorer()
    optimizer = RoadTopologyOptimizer(
        scorer,
        config=RoadOptimizerConfig(max_remove_passes=4),
    )

    plan = optimizer.optimize(city)

    assert len(plan.resulting_map.roads) < len(city.roads)
    assert plan.resulting_map.road_access(plan.resulting_map.building("res"))
    assert plan.after.total > plan.before.total
    assert any(edit.kind is RoadEditKind.REMOVE for edit in plan.edits)


def test_road_optimizer_builds_short_connector_for_unserved_building() -> None:
    city = CityMap(
        7,
        5,
        roads=frozenset({GridPoint(5, 2), GridPoint(6, 2)}),
        buildings=(building("res", 0, 1),),
    )
    scorer = LayoutScorer()
    optimizer = RoadTopologyOptimizer(
        scorer,
        config=RoadOptimizerConfig(max_connector_length=10),
    )

    plan = optimizer.optimize(city)

    assert plan.resulting_map.road_access(plan.resulting_map.building("res"))
    assert any(edit.kind is RoadEditKind.BUILD for edit in plan.edits)


def test_joint_optimizer_never_scores_below_original_layout() -> None:
    city = CityMap(
        8,
        6,
        roads=frozenset(GridPoint(x, 3) for x in range(8)),
        buildings=(
            building("res-1", 0, 2),
            building("res-2", 1, 2),
            PlacedBuilding(
                BuildingSpec(
                    "service",
                    BuildingKind.SERVICE,
                    1,
                    1,
                    service_radius=2,
                ),
                GridPoint(7, 2),
            ),
        ),
    )
    scorer = LayoutScorer()
    building_optimizer = LayoutOptimizer(
        scorer,
        config=LayoutOptimizerConfig(max_passes=2, candidate_limit_per_building=100),
    )
    road_optimizer = RoadTopologyOptimizer(scorer)
    optimizer = JointLayoutOptimizer(scorer, building_optimizer, road_optimizer)

    plan = optimizer.optimize(city)

    assert plan.after.total >= plan.before.total
    assert plan.resulting_map.width == city.width
    assert plan.resulting_map.height == city.height
