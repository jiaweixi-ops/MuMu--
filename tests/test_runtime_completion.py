from concurrent.futures import Future

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    RiskLevel,
    RunMode,
    ScreenType,
    StorageCapacity,
)
from simcity_ai_mayor.device.command_queue import DeviceCommandQueue, QueueBoundAdbWriter
from simcity_ai_mayor.executor.keeper import Keeper, ReasonCode
from simcity_ai_mayor.runtime.metrics import RuntimeMetrics
from simcity_ai_mayor.runtime.orchestrator import (
    CycleStatus,
    Observation,
    PlannedAction,
    V0Orchestrator,
)
from simcity_ai_mayor.storage.session_store import SessionStore
from simcity_ai_mayor.verifier.predicates import storage_delta


class Clock:
    def __init__(self) -> None:
        self.value = 1.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class Runner:
    def __init__(self) -> None:
        self.device_id = "mumu-0"
        self.taps = []

    def tap(self, x, y, *, _write_capability=None):
        assert _write_capability is not None
        self.taps.append((x, y))


class Observer:
    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self.frame = 0

    def observe(self) -> Observation:
        self.frame += 1
        return Observation(
            device_id="mumu-0",
            frame_id=self.frame,
            captured_at=self.clock(),
            screen=ScreenType.FACTORY,
            automation_state=AutomationState.OBSERVE,
            storage=StorageCapacity(10, 120, 1.0),
            factory_state=FactoryState.COMPLETED_COLLECTABLE,
        )


class NoActionPlanner:
    def plan(self, observation):
        del observation
        return None


class FixedPlanner:
    def __init__(self, action) -> None:
        self.action = action

    def plan(self, observation):
        del observation
        return self.action


def action() -> PlannedAction:
    return PlannedAction(
        task_id="v0:collect_factory",
        name="collect_factory",
        risk=RiskLevel.L0,
        execute=lambda writer: writer.tap(1, 2),
        verify=storage_delta(1, 1),
    )


def test_recover_time_is_not_effective_runtime(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    clock = Clock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="recover-test",
    )

    metrics.tick(AutomationState.OBSERVE)
    clock.advance(10)
    metrics.tick(AutomationState.RECOVER)
    clock.advance(100)
    metrics.tick(AutomationState.OBSERVE)

    assert metrics.tracker.effective_runtime_seconds == 10
    metrics.close()
    store.close()


def test_run_for_stops_at_duration_and_flushes(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    clock = Clock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="run-for-test",
    )
    runner = Runner()
    queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, queue)
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=Observer(clock),
        planner=NoActionPlanner(),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
        sleeper=clock.advance,
        clock=clock,
    )

    result = orchestrator.run_for(2.5, idle_sleep_seconds=0.5)

    assert result is not None
    assert result.status is CycleStatus.NO_ACTION
    assert clock.value == 3.5
    assert metrics.tracker.effective_runtime_seconds == 2.5
    queue.close()
    metrics.close()
    store.close()


def test_retry_limit_starts_explicit_action_cooldown(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    clock = Clock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="cooldown-test",
    )
    for retry in (False, True, True, True):
        metrics.record_action(
            "collect_factory",
            task_id="v0:collect_factory",
            success=False,
            retry=retry,
        )

    runner = Runner()
    queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, queue)
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=Observer(clock),
        planner=FixedPlanner(action()),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
    )

    result = orchestrator.run_once()

    assert result.status is CycleStatus.DENIED
    assert result.keeper_decision is not None
    assert result.keeper_decision.reason_code is ReasonCode.RETRY_LIMIT
    assert metrics.rate_window(
        "collect_factory", task_id="v0:collect_factory"
    ).same_action_cooling_down
    assert runner.taps == []
    queue.close()
    metrics.close()
    store.close()
