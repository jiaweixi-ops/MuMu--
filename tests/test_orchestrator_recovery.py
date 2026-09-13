from collections import deque

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    RiskLevel,
    RunMode,
    ScreenType,
    StorageCapacity,
)
from simcity_ai_mayor.device.command_queue import DeviceCommandQueue, QueueBoundAdbWriter
from simcity_ai_mayor.executor.keeper import Keeper
from simcity_ai_mayor.runtime.metrics import RuntimeMetrics
from simcity_ai_mayor.runtime.orchestrator import (
    CycleStatus,
    Observation,
    PlannedAction,
    V0Orchestrator,
)
from simcity_ai_mayor.storage.session_store import SessionStore
from simcity_ai_mayor.verifier.predicates import counter_delta
from simcity_ai_mayor.vision.observer import ObservationBuildError


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value


class FakeRunner:
    def __init__(self) -> None:
        self.device_id = "mumu-0"
        self.taps: list[tuple[int, int]] = []

    def tap(self, x: int, y: int, *, _write_capability: object | None = None) -> None:
        assert _write_capability is not None
        self.taps.append((x, y))


class SequenceObserver:
    def __init__(self, *items: Observation | Exception) -> None:
        self.items = deque(items)
        self.calls = 0

    def observe(self) -> Observation:
        self.calls += 1
        item = self.items.popleft()
        if isinstance(item, Exception):
            raise item
        return item


class FixedPlanner:
    def __init__(self, action: PlannedAction | None) -> None:
        self.action = action

    def plan(self, observation: Observation) -> PlannedAction | None:
        del observation
        return self.action


class FailingPlanner:
    def plan(self, observation: Observation) -> PlannedAction | None:
        del observation
        raise ValueError("planner state invalid")


class ExplodingOrchestrator(V0Orchestrator):
    def run_once(self):
        raise RuntimeError("unexpected loop bug")


def observation(frame_id: int, used: int = 10) -> Observation:
    return Observation(
        device_id="mumu-0",
        frame_id=frame_id,
        captured_at=float(frame_id),
        screen=ScreenType.FACTORY,
        automation_state=AutomationState.OBSERVE,
        storage=StorageCapacity(used=used, capacity=120, confidence=1.0),
        factory_state=FactoryState.COMPLETED_COLLECTABLE,
    )


def collect_action() -> PlannedAction:
    return PlannedAction(
        task_id="collect-1",
        name="collect",
        risk=RiskLevel.L0,
        execute=lambda writer: writer.tap(10, 20),
        verify=counter_delta("storage_used", 1, 1),
    )


def build_runtime(tmp_path, observer, planner):
    store = SessionStore(tmp_path / "state.db")
    clock = FakeClock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="recovery-test",
    )
    runner = FakeRunner()
    queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, queue)
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=observer,
        planner=planner,
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
        sleeper=lambda _: None,
    )
    return orchestrator, runner, queue, metrics, store


def close_runtime(queue, metrics, store) -> None:
    queue.close()
    metrics.close()
    store.close()


def test_bad_initial_observation_does_not_kill_next_cycle(tmp_path) -> None:
    observer = SequenceObserver(
        ObservationBuildError("bad PNG"),
        observation(1),
    )
    orchestrator, runner, queue, metrics, store = build_runtime(
        tmp_path,
        observer,
        FixedPlanner(None),
    )

    first = orchestrator.run_once()
    second = orchestrator.run_once()

    assert first.status is CycleStatus.OBSERVE_FAILED
    assert "observe failed" in (first.error or "")
    assert second.status is CycleStatus.NO_ACTION
    assert observer.calls == 2
    assert runner.taps == []

    close_runtime(queue, metrics, store)


def test_planner_exception_becomes_recoverable_cycle_failure(tmp_path) -> None:
    orchestrator, runner, queue, metrics, store = build_runtime(
        tmp_path,
        SequenceObserver(observation(1)),
        FailingPlanner(),
    )

    result = orchestrator.run_once()

    assert result.status is CycleStatus.OBSERVE_FAILED
    assert result.before is not None
    assert "plan failed" in (result.error or "")
    assert runner.taps == []

    close_runtime(queue, metrics, store)


def test_fresh_observe_failure_records_the_executed_action_as_failed(tmp_path) -> None:
    observer = SequenceObserver(
        observation(1, used=10),
        ObservationBuildError("fresh screenshot corrupt"),
    )
    orchestrator, runner, queue, metrics, store = build_runtime(
        tmp_path,
        observer,
        FixedPlanner(collect_action()),
    )

    result = orchestrator.run_once()

    assert result.status is CycleStatus.OBSERVE_FAILED
    assert "fresh observe failed" in (result.error or "")
    assert runner.taps == [(10, 20)]
    recent = metrics.rate_window("collect", task_id="collect-1")
    assert recent.samples_5m == 1
    assert recent.failures_5m == 1

    close_runtime(queue, metrics, store)


def test_run_stops_cleanly_after_consecutive_unhandled_loop_errors(tmp_path) -> None:
    base, runner, queue, metrics, store = build_runtime(
        tmp_path,
        SequenceObserver(observation(1)),
        FixedPlanner(None),
    )
    orchestrator = ExplodingOrchestrator(
        device_id="mumu-0",
        observer=base.observer,
        planner=base.planner,
        keeper=base.keeper,
        writer=base.writer,
        metrics=base.metrics,
        sleeper=lambda _: None,
    )

    result = orchestrator.run(
        max_cycles=10,
        idle_sleep_seconds=0.0,
        max_consecutive_loop_errors=3,
    )

    assert result is not None
    assert result.status is CycleStatus.OBSERVE_FAILED
    assert "loop error limit reached (3/3)" in (result.error or "")
    assert orchestrator.writer.command_queue.cancel_requested

    close_runtime(queue, metrics, store)
