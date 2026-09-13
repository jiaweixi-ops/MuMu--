from collections import deque

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    RiskLevel,
    RunMode,
    ScreenType,
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
    def __init__(self, observations: list[Observation]) -> None:
        self.observations = deque(observations)

    def observe(self) -> Observation:
        return self.observations.popleft()


class FixedPlanner:
    def __init__(self, action: PlannedAction | None) -> None:
        self.action = action

    def plan(self, observation: Observation) -> PlannedAction | None:
        del observation
        return self.action


def make_observation(storage_used: int = 10) -> Observation:
    return Observation(
        device_id="mumu-0",
        screen=ScreenType.FACTORY,
        automation_state=AutomationState.OBSERVE,
        factory_state=FactoryState.COMPLETED_COLLECTABLE,
        state={
            "storage_used": storage_used,
            "storage_capacity": 120,
            "storage_confidence": 1.0,
        },
    )


def make_collect_action(*, task_id: str = "collect-1") -> PlannedAction:
    return PlannedAction(
        task_id=task_id,
        name="collect",
        risk=RiskLevel.L0,
        execute=lambda writer: writer.tap(10, 20),
        verify=counter_delta("storage_used", 1, 1),
    )


def make_orchestrator(
    tmp_path,
    observations: list[Observation],
    *,
    action: PlannedAction | None,
    sleeper=lambda _: None,
    keeper: Keeper | None = None,
):
    store = SessionStore(tmp_path / "state.db")
    clock = FakeClock()
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        owner_id="backoff-retry-test",
    )
    runner = FakeRunner("mumu-0")
    command_queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, command_queue)
    orchestrator = V0Orchestrator(
        device_id="mumu-0",
        observer=FakeObserver(observations),
        planner=FixedPlanner(action),
        keeper=keeper or Keeper(device_id="mumu-0", run_mode=RunMode.AUTO),
        writer=writer,
        metrics=metrics,
        sleeper=sleeper,
    )
    return orchestrator, runner, command_queue, metrics, store


def test_same_task_failures_reach_keeper_retry_limit(tmp_path) -> None:
    observations = [make_observation() for _ in range(9)]
    keeper = Keeper(
        device_id="mumu-0",
        run_mode=RunMode.AUTO,
        circuit_breaker_min_samples=100,
    )
    orchestrator, runner, command_queue, metrics, store = make_orchestrator(
        tmp_path,
        observations,
        action=make_collect_action(),
        keeper=keeper,
    )

    results = [orchestrator.run_once() for _ in range(5)]

    assert [result.status for result in results[:4]] == [
        CycleStatus.VERIFICATION_FAILED,
    ] * 4
    assert results[4].status is CycleStatus.DENIED
    assert results[4].keeper_decision is not None
    assert results[4].keeper_decision.reason_code is ReasonCode.RETRY_LIMIT
    assert runner.taps == [(10, 20)] * 4
    recent = metrics.rate_window("collect", task_id="collect-1")
    assert recent.retries_for_action == 3

    command_queue.close()
    metrics.close()
    store.close()


def test_retry_history_is_scoped_by_task_id(tmp_path) -> None:
    observations = [make_observation() for _ in range(8)]
    orchestrator, _, command_queue, metrics, store = make_orchestrator(
        tmp_path,
        observations,
        action=make_collect_action(task_id="task-a"),
    )

    for _ in range(3):
        result = orchestrator.run_once()
        assert result.status is CycleStatus.VERIFICATION_FAILED

    assert metrics.is_retry("task-a", "collect")
    assert not metrics.is_retry("task-b", "collect")
    assert metrics.rate_window("collect", task_id="task-a").retries_for_action == 2
    assert metrics.rate_window("collect", task_id="task-b").retries_for_action == 0

    command_queue.close()
    metrics.close()
    store.close()


def test_run_uses_longer_no_action_backoff(tmp_path) -> None:
    sleeps: list[float] = []
    orchestrator, _, command_queue, metrics, store = make_orchestrator(
        tmp_path,
        [make_observation(), make_observation()],
        action=None,
        sleeper=sleeps.append,
    )

    result = orchestrator.run(max_cycles=2, idle_sleep_seconds=0.2)

    assert result is not None
    assert result.status is CycleStatus.NO_ACTION
    assert sleeps == [1.0, 1.0]

    command_queue.close()
    metrics.close()
    store.close()


def test_run_exponentially_backs_off_consecutive_failures(tmp_path) -> None:
    sleeps: list[float] = []
    orchestrator, _, command_queue, metrics, store = make_orchestrator(
        tmp_path,
        [make_observation() for _ in range(6)],
        action=make_collect_action(),
        sleeper=sleeps.append,
    )

    result = orchestrator.run(max_cycles=3, idle_sleep_seconds=0.2)

    assert result is not None
    assert result.status is CycleStatus.VERIFICATION_FAILED
    assert sleeps == [0.2, 0.4, 0.8]

    command_queue.close()
    metrics.close()
    store.close()
