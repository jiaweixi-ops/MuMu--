import time
from collections import deque
from itertools import count

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
    AcceptanceEvent,
    CycleStatus,
    Observation,
    PlannedAction,
    V0Orchestrator,
)
from simcity_ai_mayor.storage.session_store import SessionStore
from simcity_ai_mayor.verifier.predicates import counter_delta

_FRAME_IDS = count(1)


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


class SlowOnceRunner(FakeRunner):
    def __init__(self, device_id: str) -> None:
        super().__init__(device_id)
        self._slow_next = True

    def tap(self, x: int, y: int, *, _write_capability: object | None = None) -> None:
        if self._slow_next:
            self._slow_next = False
            time.sleep(0.05)
        super().tap(x, y, _write_capability=_write_capability)


class FakeObserver:
    def __init__(self, *observations: Observation) -> None:
        self.observations = deque(observations)
        self.calls = 0

    def observe(self) -> Observation:
        self.calls += 1
        return self.observations.popleft()


class InterruptingObserver:
    def __init__(self) -> None:
        self.calls = 0

    def observe(self) -> Observation:
        self.calls += 1
        raise KeyboardInterrupt


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


def make_interrupt_action() -> PlannedAction:
    def execute(writer: QueueBoundAdbWriter):
        del writer
        raise KeyboardInterrupt

    return PlannedAction(
        task_id="interrupt-1",
        name="interrupt",
        risk=RiskLevel.L0,
        execute=execute,
        verify=counter_delta("storage_used", 1, 1),
    )


def make_observation(
    storage_used: int,
    factory_state: FactoryState,
    *,
    automation_state: AutomationState = AutomationState.OBSERVE,
    storage_capacity: int = 120,
    storage_confidence: float = 1.0,
    frame_id: int | None = None,
    captured_at: float | None = None,
) -> Observation:
    actual_frame_id = frame_id if frame_id is not None else next(_FRAME_IDS)
    actual_captured_at = (
        captured_at if captured_at is not None else float(actual_frame_id)
    )
    return Observation(
        device_id="mumu-0",
        frame_id=actual_frame_id,
        captured_at=actual_captured_at,
        screen=ScreenType.FACTORY,
        automation_state=automation_state,
        storage=StorageCapacity(
            used=storage_used,
            capacity=storage_capacity,
            confidence=storage_confidence,
        ),
        factory_state=factory_state,
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


def test_orchestrator_rejects_stale_after_frame_before_business_verifier(tmp_path) -> None:
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
        make_observation(
            10,
            FactoryState.COMPLETED_COLLECTABLE,
            frame_id=500,
            captured_at=500.0,
        ),
        make_observation(
            11,
            FactoryState.COLLECTED,
            frame_id=500,
            captured_at=500.0,
        ),
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
    assert result.verification is not None
    assert "frame_id did not advance" in result.verification.reason
    assert "captured_at did not advance" in result.verification.reason
    assert metrics.tracker.collect_success == 0
    assert metrics.rate_window("collect", task_id="collect-1").failures_5m == 1

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


def test_blocked_storage_recovery_waits_for_trusted_free_space(tmp_path) -> None:
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
        make_observation(
            120,
            FactoryState.COMPLETED_STORAGE_BLOCKED,
            automation_state=AutomationState.BLOCKED_STORAGE,
        ),
        make_observation(
            110,
            FactoryState.IDLE,
            automation_state=AutomationState.STORAGE_NORMAL,
            storage_confidence=0.50,
        ),
        make_observation(
            110,
            FactoryState.IDLE,
            automation_state=AutomationState.STORAGE_NORMAL,
            storage_confidence=1.0,
        ),
    )
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=observer,
        planner=FixedPlanner(None),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
    )

    first = orchestrator.run_once()
    assert first.status is CycleStatus.NO_ACTION
    assert metrics.tracker.blocked_storage_detected
    assert not metrics.tracker.manual_clear_recovered

    second = orchestrator.run_once()
    assert second.status is CycleStatus.NO_ACTION
    assert not metrics.tracker.manual_clear_recovered

    third = orchestrator.run_once()
    assert third.status is CycleStatus.NO_ACTION
    assert metrics.tracker.manual_clear_recovered

    command_queue.close()
    metrics.close()
    store.close()


def test_run_stops_immediately_when_human_approval_is_required(tmp_path) -> None:
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
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.ASSIST),
        writer=writer,
        metrics=metrics,
        sleeper=lambda _: None,
    )

    result = orchestrator.run(max_cycles=6, idle_sleep_seconds=0.0)

    assert result is not None
    assert result.status is CycleStatus.NEEDS_HUMAN
    assert observer.calls == 1
    assert runner.taps == []

    command_queue.close()
    metrics.close()
    store.close()


def test_action_timeout_reopens_writer_for_next_cycle(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    clock = FakeClock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="orchestrator-test",
    )
    runner = SlowOnceRunner("mumu-0")
    command_queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, command_queue)
    observer = FakeObserver(
        make_observation(10, FactoryState.COMPLETED_COLLECTABLE),
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
        action_timeout_seconds=0.01,
    )

    first = orchestrator.run_once()

    assert first.status is CycleStatus.EXECUTION_FAILED
    assert not writer.command_queue.cancel_requested
    assert observer.calls == 1
    assert runner.taps == [(10, 20)]
    assert metrics.rate_window("collect").failures_5m == 1

    second = orchestrator.run_once()

    assert second.status is CycleStatus.VERIFIED
    assert not writer.command_queue.cancel_requested
    assert observer.calls == 3
    assert runner.taps == [(10, 20), (10, 20)]

    command_queue.close()
    metrics.close()
    store.close()


def test_keyboard_interrupt_during_execute_latches_emergency_stop(tmp_path) -> None:
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
        planner=FixedPlanner(make_interrupt_action()),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
    )

    result = orchestrator.run_once()

    assert result.status is CycleStatus.EMERGENCY_STOP
    assert writer.command_queue.cancel_requested
    assert observer.calls == 1
    assert metrics.rate_window("interrupt").samples_5m == 0
    assert "KeyboardInterrupt" in (result.error or "")

    command_queue.close()
    metrics.close()
    store.close()


def test_run_maps_observer_keyboard_interrupt_to_emergency_stop(tmp_path) -> None:
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
    observer = InterruptingObserver()
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=observer,
        planner=FixedPlanner(None),
        keeper=Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
    )

    result = orchestrator.run(max_cycles=3, idle_sleep_seconds=0.0)

    assert result is not None
    assert result.status is CycleStatus.EMERGENCY_STOP
    assert writer.command_queue.cancel_requested
    assert observer.calls == 1

    command_queue.close()
    metrics.close()
    store.close()
