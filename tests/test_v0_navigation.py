from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    ScreenType,
    StorageCapacity,
)
from simcity_ai_mayor.planner.v0 import TapPoint, V0Planner
from simcity_ai_mayor.runtime.orchestrator import Observation, PlannedAction
from simcity_ai_mayor.verifier.predicates import Verdict


class Store:
    def total_output(self, device_id):
        del device_id
        return 0

    def item_output(self, device_id, item):
        del device_id, item
        return 0

    def record_output(self, device_id, item, count=1):
        del device_id, item, count


class Gate:
    def __init__(self) -> None:
        self.expected = None

    def expect_delta(self, minimum, maximum):
        self.expected = (minimum, maximum)


class Writer:
    def __init__(self) -> None:
        self.taps = []

    def tap(self, x, y):
        self.taps.append((x, y))
        return object()


def observation(
    *,
    screen=ScreenType.CITY,
    factory_state=FactoryState.UNKNOWN,
    storage=None,
) -> Observation:
    return Observation(
        device_id="mumu-0",
        frame_id=1,
        captured_at=1.0,
        screen=screen,
        automation_state=AutomationState.OBSERVE,
        storage=storage,
        factory_state=factory_state,
    )


def test_city_screen_plans_enter_factory_action() -> None:
    planner = V0Planner(
        device_id="mumu-0",
        session_store=Store(),
        enter_factory_tap=TapPoint(100, 200),
    )

    action = planner.plan(observation())

    assert isinstance(action, PlannedAction)
    assert action.name == "enter_factory"
    writer = Writer()
    action.execute(writer)
    assert writer.taps == [(100, 200)]
    result = action.verify(
        {"screen": ScreenType.CITY.value},
        {"screen": ScreenType.FACTORY.value},
    )
    assert result.verdict is Verdict.PASS


def test_collect_registers_expected_storage_delta_before_tap() -> None:
    gate = Gate()
    planner = V0Planner(
        device_id="mumu-0",
        session_store=Store(),
        collect_tap=TapPoint(10, 20),
        storage_gate=gate,
    )
    obs = observation(
        screen=ScreenType.FACTORY,
        factory_state=FactoryState.COMPLETED_COLLECTABLE,
        storage=StorageCapacity(10, 100, 1.0),
    )

    action = planner.plan(obs)

    assert isinstance(action, PlannedAction)
    writer = Writer()
    action.execute(writer)
    assert gate.expected == (1, 1)
    assert writer.taps == [(10, 20)]
