from collections import deque

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    RiskLevel,
    RunMode,
    ScreenType,
)
from simcity_ai_mayor.device.command_queue import DeviceCommandQueue, QueueBoundAdbWriter
from simcity_ai_mayor.executor.keeper import Keeper
from simcity_ai_mayor.runtime.metrics import RuntimeMetrics
from simcity_ai_mayor.runtime.orchestrator import (
    AcceptanceEvent,
    CycleStatus,
    Observation,
    PlannedAction,
    V0Orchestrator,
)
from simcity_ai_mayor.storage.session_store import SessionStore
from simcity_ai_mayor.verifier.predicates import counter_delta


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeRunner:
    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int, *, _write_capability: object | None = None) -> None:
        assert _write_capability is not None
        self.taps.append((x, y))


class FakeObserver:
    def __init__(self, *observations: Observation) -> None:
        self.observations = deque(observations)
        self.calls = 0

    def observe(self) -> Observation:
        self.calls += 1
        return self.observations.popleft()


class FixedPlanner:
    def __init__(self, action: PlannedAction | None) -> None:
        self.action = action

    def plan(self, observation: Observation) -> PlannedAction | None:
        del observation
        return self.action


def make_collect_action() -> PlannedAction:
    return PlannedAction(
        task_id="collect-1",
        name="collect",
        risk=RiskLevel.L0,
        execute=lambda writer: writer.tap(10, 20),
        verify=counter_delta("storage_used", 1, 1),
        acceptance_event=AcceptanceEvent.COLLECT,
    )


def make_observation(
    storage_used: int,
    factory_state: FactoryState,
) -> Observation:
    return Observation(
        device_id="mumu-0",
        screen=ScreenType.FACTORY,
        automation_state=AutomationState.OBSERVE,
        factory_state=factory_state,
        state={"storage_used": storage_used},
    )


def test_orchestrator_executes_through_queue_and_fresh_verifies(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    clock = FakeClock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="orchestrator-test",
    )
    runner = FakeRunner("mumu-0")
    command_queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, command_queue)
    observer = FakeObserver(
        make_observation(10, FactoryState.COMPLETED_COLLECTABLE),
        make_observation(11, FactoryState.COLLECTED),
    )
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=observer,
        planner=FixedPlanner(make_collect_action()),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
    )

    result = orchestrator.run_once()

    assert result.status is CycleStatus.VERIFIED
    assert observer.calls == 2
    assert runner.taps == [(10, 20)]
    assert metrics.tracker.collect_success == 1
    assert metrics.tracker.transitions == {
        "COMPLETED_COLLECTABLE->COLLECTED": 1,
    }
    assert metrics.rate_window("collect").samples_5m == 1

    command_queue.close()
    metrics.close()
    store.close()


def test_orchestrator_keeper_denial_never_writes(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    clock = FakeClock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="orchestrator-test",
    )
    runner = FakeRunner("mumu-0")
    command_queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, command_queue)
    observer = FakeObserver(make_observation(10, FactoryState.COMPLETED_COLLECTABLE))
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=observer,
        planner=FixedPlanner(make_collect_action()),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.DRY_RUN),
        writer=writer,
        metrics=metrics,
    )

    result = orchestrator.run_once()

    assert result.status is CycleStatus.DENIED
    assert observer.calls == 1
    assert runner.taps == []

    command_queue.close()
    metrics.close()
    store.close()


def test_orchestrator_failed_fresh_verify_records_failed_action(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    clock = FakeClock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="orchestrator-test",
    )
    runner = FakeRunner("mumu-0")
    command_queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, command_queue)
    observer = FakeObserver(
        make_observation(10, FactoryState.COMPLETED_COLLECTABLE),
        make_observation(10, FactoryState.COMPLETED_COLLECTABLE),
    )
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=observer,
        planner=FixedPlanner(make_collect_action()),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
    )

    result = orchestrator.run_once()

    assert result.status is CycleStatus.VERIFICATION_FAILED
    assert observer.calls == 2
    assert runner.taps == [(10, 20)]
    assert metrics.tracker.collect_success == 0
    recent = metrics.rate_window("collect")
    assert recent.samples_5m == 1
    assert recent.failures_5m == 1

    command_queue.close()
    metrics.close()
    store.close()
