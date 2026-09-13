from concurrent.futures import Future

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    ScreenType,
    StorageCapacity,
    V0Policy,
)
from simcity_ai_mayor.planner.v0 import ProductionRecipe, TapPoint, V0Planner
from simcity_ai_mayor.runtime.orchestrator import Observation, PlannedAction, PlanningResult
from simcity_ai_mayor.verifier.predicates import Verdict


class FakeSessionStore:
    def __init__(self) -> None:
        self.total = 0
        self.items: dict[str, int] = {}

    def total_output(self, device_id: str) -> int:
        assert device_id == "mumu-0"
        return self.total

    def item_output(self, device_id: str, item: str) -> int:
        assert device_id == "mumu-0"
        return self.items.get(item, 0)

    def record_output(self, device_id: str, item: str, count: int = 1) -> None:
        assert device_id == "mumu-0"
        self.total += count
        self.items[item] = self.items.get(item, 0) + count


class FakeWriter:
    def __init__(self) -> None:
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int):
        self.taps.append((x, y))
        future = Future()
        future.set_result(None)
        return future


def observation(
    state: FactoryState,
    *,
    used: int = 10,
    capacity: int = 120,
    confidence: float = 1.0,
) -> Observation:
    return Observation(
        device_id="mumu-0",
        frame_id=1,
        captured_at=1.0,
        screen=ScreenType.FACTORY,
        automation_state=AutomationState.OBSERVE,
        storage=StorageCapacity(used, capacity, confidence),
        factory_state=state,
    )


def test_collect_plan_is_single_item_and_storage_verified() -> None:
    store = FakeSessionStore()
    planner = V0Planner(
        device_id="mumu-0",
        session_store=store,
        collect_tap=TapPoint(10, 20),
    )

    planned = planner.plan(observation(FactoryState.COMPLETED_COLLECTABLE))

    assert isinstance(planned, PlannedAction)
    writer = FakeWriter()
    planned.execute(writer).result()
    assert writer.taps == [(10, 20)]
    result = planned.verify(
        {"storage_used": 10},
        {"storage_used": 11},
    )
    assert result.verdict is Verdict.PASS


def test_collect_plan_waits_for_trusted_storage() -> None:
    planner = V0Planner(
        device_id="mumu-0",
        session_store=FakeSessionStore(),
        collect_tap=TapPoint(10, 20),
    )

    planned = planner.plan(
        observation(FactoryState.COMPLETED_COLLECTABLE, confidence=0.5)
    )

    assert isinstance(planned, PlanningResult)
    assert planned.action is None
    assert planned.state is AutomationState.WAIT_OCR_UNTRUSTED


def test_production_plan_predebits_persistent_session_cap() -> None:
    store = FakeSessionStore()
    planner = V0Planner(
        device_id="mumu-0",
        session_store=store,
        production=ProductionRecipe("metal", TapPoint(30, 40)),
    )

    planned = planner.plan(observation(FactoryState.IDLE))

    assert isinstance(planned, PlannedAction)
    writer = FakeWriter()
    planned.execute(writer).result()
    assert store.total == 1
    assert store.items == {"metal": 1}
    assert writer.taps == [(30, 40)]
    verification = planned.verify(
        {"factory_state": FactoryState.IDLE.value},
        {"factory_state": FactoryState.PRODUCING.value},
    )
    assert verification.verdict is Verdict.PASS


def test_production_plan_exposes_wait_session_cap_state() -> None:
    store = FakeSessionStore()
    store.total = 12
    planner = V0Planner(
        device_id="mumu-0",
        session_store=store,
        policy=V0Policy(max_total_session_output=12),
        production=ProductionRecipe("metal", TapPoint(30, 40)),
    )

    planned = planner.plan(observation(FactoryState.IDLE))

    assert isinstance(planned, PlanningResult)
    assert planned.action is None
    assert planned.state is AutomationState.WAIT_SESSION_CAP
