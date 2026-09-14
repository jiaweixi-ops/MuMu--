from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    BuildingSpec,
    CityMap,
    GridPoint,
    PlacedBuilding,
)
from simcity_ai_mayor.layout.construction import ConstructionPlanner
from simcity_ai_mayor.layout.joint_optimizer import JointLayoutPlan
from simcity_ai_mayor.layout.optimizer import LayoutMove
from simcity_ai_mayor.layout.scorer import LayoutScore
from simcity_ai_mayor.layout.staging import StagingSolver


def score(value: float = 0.0) -> LayoutScore:
    return LayoutScore(
        total=value,
        road_access=1.0,
        service_coverage=1.0,
        beauty_coverage=1.0,
        pollution_separation=1.0,
        traffic_efficiency=1.0,
        zone_compactness=1.0,
        expansion_space=1.0,
        road_efficiency=1.0,
        move_penalty=0.0,
    )


def building(building_id: str, x: int, y: int) -> PlacedBuilding:
    return PlacedBuilding(
        BuildingSpec(building_id, BuildingKind.RESIDENTIAL, 1, 1),
        GridPoint(x, y),
    )


def test_staging_solver_turns_two_building_swap_into_executable_sequence() -> None:
    roads = frozenset(GridPoint(x, 1) for x in range(3))
    original = CityMap(
        4,
        3,
        roads=roads,
        buildings=(building("a", 0, 0), building("b", 1, 0)),
    )
    target_map = CityMap(
        4,
        3,
        roads=roads,
        buildings=(building("a", 1, 0), building("b", 0, 0)),
    )
    target = JointLayoutPlan(
        before=score(),
        after=score(1.0),
        moves=(
            LayoutMove("a", GridPoint(0, 0), GridPoint(1, 0)),
            LayoutMove("b", GridPoint(1, 0), GridPoint(0, 0)),
        ),
        road_edits=(),
        resulting_map=target_map,
    )
    construction = ConstructionPlanner().build(original, target)

    resolution = StagingSolver().resolve(original, target, construction)

    assert resolution.fully_resolved
    assert resolution.staging_points == (("a", GridPoint(2, 0)),)
    assert [step.step_id for step in resolution.plan.ordered_steps] == [
        "staging:a:1",
        "move:b",
        "move:a",
    ]
    assert resolution.plan.ordered_steps[0].destination == GridPoint(2, 0)
    assert resolution.plan.ordered_steps[-1].source == GridPoint(2, 0)
