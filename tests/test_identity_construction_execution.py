from dataclasses import replace

import pytest

from simcity_ai_mayor.city.identity import (
    BuildingIdentityReconciler,
    IdentityReconciliationError,
)
from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    BuildingSpec,
    CityMap,
    GridPoint,
    PlacedBuilding,
)
from simcity_ai_mayor.layout.construction import (
    ConstructionPlan,
    ConstructionStep,
    ConstructionStepKind,
)
from simcity_ai_mayor.layout.execution import (
    ConstructionExecutionStatus,
    ConstructionExecutor,
)


def building(building_id: str, x: int, y: int) -> PlacedBuilding:
    return PlacedBuilding(
        BuildingSpec(building_id, BuildingKind.RESIDENTIAL, 1, 1),
        GridPoint(x, y),
    )


def snapshot(city: CityMap) -> CityMap:
    buildings = tuple(
        PlacedBuilding(
            replace(
                item.spec,
                building_id=(
                    f"{item.spec.building_id.split('@', 1)[0]}@"
                    f"{item.origin.x},{item.origin.y}"
                ),
            ),
            item.origin,
        )
        for item in city.buildings
    )
    return city.with_buildings(buildings)


def test_reconciler_preserves_identity_for_explicit_move_between_same_type_buildings() -> None:
    previous = CityMap(
        4,
        3,
        roads=frozenset(GridPoint(x, 1) for x in range(4)),
        buildings=(building("res@0,0", 0, 0), building("res@1,0", 1, 0)),
    )
    scanned = CityMap(
        4,
        3,
        roads=previous.roads,
        buildings=(building("res@2,0", 2, 0), building("res@1,0", 1, 0)),
    )

    result = BuildingIdentityReconciler().reconcile(
        previous,
        scanned,
        expected_moves={"res@0,0": GridPoint(2, 0)},
    )

    assert result.city.building("res@0,0").origin == GridPoint(2, 0)
    assert result.city.building("res@1,0").origin == GridPoint(1, 0)


def test_reconciler_rejects_unexpected_building_motion() -> None:
    previous = CityMap(
        4,
        3,
        roads=frozenset(GridPoint(x, 1) for x in range(4)),
        buildings=(building("res@0,0", 0, 0),),
    )
    scanned = CityMap(
        4,
        3,
        roads=previous.roads,
        buildings=(building("res@2,0", 2, 0),),
    )

    with pytest.raises(IdentityReconciliationError):
        BuildingIdentityReconciler().reconcile(previous, scanned)


class FakeWorld:
    def __init__(self, state: CityMap, *, inject_extra_road: bool = False) -> None:
        self.state = state
        self.inject_extra_road = inject_extra_road
        self.scan_calls = 0
        self.action_calls = 0

    def scan(self) -> CityMap:
        self.scan_calls += 1
        return snapshot(self.state)

    def execute(self, step: ConstructionStep, current: CityMap) -> None:
        self.action_calls += 1
        if step.kind is ConstructionStepKind.MOVE_BUILDING:
            assert step.building_id is not None
            assert step.destination is not None
            self.state = self.state.move_building(step.building_id, step.destination)
            if self.inject_extra_road:
                self.state = replace(
                    self.state,
                    roads=self.state.roads | {GridPoint(3, 2)},
                )
            return
        assert step.point is not None
        if step.kind is ConstructionStepKind.BUILD_ROAD:
            self.state = replace(self.state, roads=self.state.roads | {step.point})
        elif step.kind is ConstructionStepKind.REMOVE_ROAD:
            self.state = replace(self.state, roads=self.state.roads - {step.point})
        else:  # pragma: no cover - enum exhaustiveness guard
            raise AssertionError(step.kind)


def baseline_city() -> CityMap:
    return CityMap(
        4,
        3,
        roads=frozenset(GridPoint(x, 1) for x in range(4)),
        buildings=(building("res@0,0", 0, 0),),
    )


def test_construction_executor_reconciles_after_each_verified_step() -> None:
    baseline = baseline_city()
    move = ConstructionStep(
        "move:res",
        ConstructionStepKind.MOVE_BUILDING,
        building_id="res@0,0",
        source=GridPoint(0, 0),
        destination=GridPoint(2, 0),
    )
    remove = ConstructionStep(
        "road:remove:0:1",
        ConstructionStepKind.REMOVE_ROAD,
        point=GridPoint(0, 1),
        depends_on=(move.step_id,),
    )
    world = FakeWorld(baseline)

    report = ConstructionExecutor(adapter=world, scanner=world).run(
        baseline,
        ConstructionPlan((move, remove), ()),
    )

    assert report.status is ConstructionExecutionStatus.COMPLETE
    assert len(report.completed_steps) == 2
    assert report.current_map.building("res@0,0").origin == GridPoint(2, 0)
    assert GridPoint(0, 1) not in report.current_map.roads
    assert world.scan_calls == 3


def test_construction_executor_stops_on_undeclared_map_change() -> None:
    baseline = baseline_city()
    move = ConstructionStep(
        "move:res",
        ConstructionStepKind.MOVE_BUILDING,
        building_id="res@0,0",
        source=GridPoint(0, 0),
        destination=GridPoint(2, 0),
    )
    world = FakeWorld(baseline, inject_extra_road=True)

    report = ConstructionExecutor(adapter=world, scanner=world).run(
        baseline,
        ConstructionPlan((move,), ()),
    )

    assert report.status is ConstructionExecutionStatus.VERIFICATION_FAILED
    assert report.failed_step == move
    assert not report.completed_steps


def test_construction_executor_refuses_unresolved_plan_before_touching_game() -> None:
    baseline = baseline_city()
    unresolved = ConstructionStep(
        "move:res",
        ConstructionStepKind.MOVE_BUILDING,
        building_id="res@0,0",
        source=GridPoint(0, 0),
        destination=GridPoint(2, 0),
    )
    world = FakeWorld(baseline)

    report = ConstructionExecutor(adapter=world, scanner=world).run(
        baseline,
        ConstructionPlan((), (unresolved,)),
    )

    assert report.status is ConstructionExecutionStatus.BLOCKED_UNRESOLVED
    assert world.action_calls == 0
    assert world.scan_calls == 0
