from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    BuildingSpec,
    CityMap,
    GridPoint,
    PlacedBuilding,
)
from simcity_ai_mayor.layout.construction import (
    ConstructionPlanner,
    ConstructionStepKind,
)
from simcity_ai_mayor.layout.joint_optimizer import JointLayoutPlan
from simcity_ai_mayor.layout.optimizer import LayoutMove
from simcity_ai_mayor.layout.road_optimizer import RoadEdit, RoadEditKind
from simcity_ai_mayor.layout.scorer import LayoutScore


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


def spec(building_id: str) -> BuildingSpec:
    return BuildingSpec(building_id, BuildingKind.RESIDENTIAL, 1, 1)


def test_building_swap_is_flagged_as_requiring_staging() -> None:
    road = frozenset({GridPoint(0, 1), GridPoint(1, 1)})
    original = CityMap(
        3,
        3,
        roads=road,
        buildings=(
            PlacedBuilding(spec("a"), GridPoint(0, 0)),
            PlacedBuilding(spec("b"), GridPoint(1, 0)),
        ),
    )
    result = CityMap(
        3,
        3,
        roads=road,
        buildings=(
            PlacedBuilding(spec("a"), GridPoint(1, 0)),
            PlacedBuilding(spec("b"), GridPoint(0, 0)),
        ),
    )
    plan = JointLayoutPlan(
        before=score(),
        after=score(1.0),
        moves=(
            LayoutMove("a", GridPoint(0, 0), GridPoint(1, 0)),
            LayoutMove("b", GridPoint(1, 0), GridPoint(0, 0)),
        ),
        road_edits=(),
        resulting_map=result,
    )

    construction = ConstructionPlanner().build(original, plan)

    assert construction.requires_staging
    assert {step.step_id for step in construction.unresolved_steps} == {"move:a", "move:b"}


def test_road_removal_is_ordered_after_moves_and_road_builds() -> None:
    original = CityMap(
        4,
        3,
        roads=frozenset({GridPoint(0, 1), GridPoint(1, 1), GridPoint(2, 1)}),
        buildings=(PlacedBuilding(spec("a"), GridPoint(0, 0)),),
    )
    result = CityMap(
        4,
        3,
        roads=frozenset({GridPoint(1, 1), GridPoint(2, 1), GridPoint(3, 1)}),
        buildings=(PlacedBuilding(spec("a"), GridPoint(2, 0)),),
    )
    plan = JointLayoutPlan(
        before=score(),
        after=score(1.0),
        moves=(LayoutMove("a", GridPoint(0, 0), GridPoint(2, 0)),),
        road_edits=(
            RoadEdit(RoadEditKind.BUILD, GridPoint(3, 1)),
            RoadEdit(RoadEditKind.REMOVE, GridPoint(0, 1)),
        ),
        resulting_map=result,
    )

    construction = ConstructionPlanner().build(original, plan)

    assert not construction.requires_staging
    ids = [step.step_id for step in construction.ordered_steps]
    remove_index = ids.index("road:remove:0:1")
    assert ids.index("move:a") < remove_index
    assert ids.index("road:add:3:1") < remove_index
    remove_step = construction.ordered_steps[remove_index]
    assert remove_step.kind is ConstructionStepKind.REMOVE_ROAD
