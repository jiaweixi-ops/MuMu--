import pytest

from simcity_ai_mayor.core.models import AutomationState, FactoryState
from simcity_ai_mayor.runtime.metrics import RuntimeMetrics
from simcity_ai_mayor.storage.session_store import MetricsLeaseConflict, SessionStore


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def test_runtime_metrics_advances_effective_and_wait_time(tmp_path) -> None:
    clock = FakeClock()
    store = SessionStore(tmp_path / "state.db")
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        persist_interval_seconds=10.0,
    )

    metrics.tick(AutomationState.EXECUTE)
    clock.advance(120.0)
    metrics.tick(AutomationState.WAIT_SESSION_CAP)
    clock.advance(30.0)
    metrics.tick(AutomationState.WAIT_SESSION_CAP)
    clock.advance(30.0)
    metrics.tick(AutomationState.PAUSED)
    clock.advance(100.0)
    metrics.tick(AutomationState.PAUSED)
    metrics.flush()

    tracker = store.load_acceptance("mumu-0")
    assert tracker.effective_runtime_seconds == 180.0
    assert tracker.wait_session_cap_seconds == 60.0
    metrics.close()
    store.close()


def test_blocked_storage_time_is_not_effective_runtime(tmp_path) -> None:
    clock = FakeClock()
    store = SessionStore(tmp_path / "state.db")
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        persist_interval_seconds=10.0,
    )

    metrics.tick(AutomationState.BLOCKED_STORAGE)
    clock.advance(5 * 3600)
    metrics.tick(AutomationState.BLOCKED_STORAGE)
    metrics.flush()

    tracker = store.load_acceptance("mumu-0")
    assert tracker.effective_runtime_seconds == 0.0
    assert tracker.blocked_storage_seconds == 5 * 3600
    metrics.close()
    store.close()


def test_failure_window_is_fixed_to_five_minutes(tmp_path) -> None:
    clock = FakeClock()
    store = SessionStore(tmp_path / "state.db")
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        persist_interval_seconds=30.0,
        event_retention_seconds=600.0,
    )

    metrics.record_action("collect", success=False)
    clock.advance(400.0)
    metrics.record_action("collect", success=True)

    recent = metrics.rate_window("collect")
    assert recent.samples_5m == 1
    assert recent.failures_5m == 0
    metrics.close()
    store.close()


def test_event_retention_cannot_be_shorter_than_failure_window(tmp_path) -> None:
    store = SessionStore(tmp_path / "state.db")
    with pytest.raises(ValueError, match="event_retention_seconds"):
        RuntimeMetrics(
            device_id="mumu-0",
            store=store,
            event_retention_seconds=299.0,
        )
    store.close()


def test_rate_window_uses_monotonic_sliding_window_and_cooldown(tmp_path) -> None:
    clock = FakeClock()
    store = SessionStore(tmp_path / "state.db")
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        persist_interval_seconds=30.0,
    )

    metrics.record_action("collect", success=False, retry=True)
    clock.advance(1.0)
    metrics.record_action("collect", success=False, retry=True)
    clock.advance(1.0)
    metrics.record_action("collect", success=False, retry=True)
    metrics.start_action_cooldown("collect", 120.0)

    recent = metrics.rate_window("collect")
    assert recent.actions_last_minute == 3
    assert recent.samples_5m == 3
    assert recent.failures_5m == 3
    assert recent.retries_for_action == 3
    assert recent.same_action_cooling_down

    clock.advance(301.0)
    expired = metrics.rate_window("collect")
    assert expired.actions_last_minute == 0
    assert expired.samples_5m == 0
    assert expired.failures_5m == 0
    assert expired.retries_for_action == 0
    assert not expired.same_action_cooling_down
    metrics.close()
    store.close()


def test_acceptance_updates_persist_across_store_reopen(tmp_path) -> None:
    path = tmp_path / "state.db"
    clock = FakeClock()
    store = SessionStore(path)
    metrics = RuntimeMetrics(
        device_id="mumu-0",
        store=store,
        clock=clock,
        lease_clock=clock,
        persist_interval_seconds=10.0,
    )

    metrics.record_collect_success(2)
    metrics.record_produce_success(3)
    metrics.record_transition(FactoryState.IDLE, FactoryState.PRODUCING)
    metrics.mark_blocked_storage_detected()
    metrics.mark_manual_clear_recovered()
    metrics.close()
    store.close()

    reopened = SessionStore(path)
    tracker = reopened.load_acceptance("mumu-0")
    assert tracker.collect_success == 2
    assert tracker.produce_success == 3
    assert tracker.transitions == {"IDLE->PRODUCING": 1}
    assert tracker.blocked_storage_detected
    assert tracker.manual_clear_recovered
    reopened.close()


def test_same_device_rejects_second_runtime_metrics_owner(tmp_path) -> None:
    path = tmp_path / "state.db"
    clock = FakeClock()
    first_store = SessionStore(path)
    second_store = SessionStore(path)
    first = RuntimeMetrics(
        device_id="mumu-0",
        store=first_store,
        clock=clock,
        lease_clock=clock,
        owner_id="owner-1",
    )

    with pytest.raises(MetricsLeaseConflict):
        RuntimeMetrics(
            device_id="mumu-0",
            store=second_store,
            clock=clock,
            lease_clock=clock,
            owner_id="owner-2",
        )

    first.close()
    replacement = RuntimeMetrics(
        device_id="mumu-0",
        store=second_store,
        clock=clock,
        lease_clock=clock,
        owner_id="owner-2",
    )
    replacement.close()
    first_store.close()
    second_store.close()
