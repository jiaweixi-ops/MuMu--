from __future__ import annotations

import time
import uuid
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from typing import Protocol

from simcity_ai_mayor.core.acceptance import V0AcceptanceTracker
from simcity_ai_mayor.core.models import AutomationState, FactoryState
from simcity_ai_mayor.executor.keeper import RateWindow

RATE_WINDOW_SECONDS = 60.0
FAILURE_WINDOW_SECONDS = 300.0


class AcceptanceStore(Protocol):
    def load_acceptance(self, device_id: str) -> V0AcceptanceTracker: ...

    def save_acceptance(self, device_id: str, tracker: V0AcceptanceTracker) -> None: ...

    def acquire_metrics_lease(
        self,
        device_id: str,
        owner_id: str,
        *,
        lease_seconds: float = 120.0,
        now_epoch: float | None = None,
    ) -> None: ...

    def renew_metrics_lease(
        self,
        device_id: str,
        owner_id: str,
        *,
        lease_seconds: float = 120.0,
        now_epoch: float | None = None,
    ) -> None: ...

    def release_metrics_lease(self, device_id: str, owner_id: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ActionSample:
    timestamp: float
    action_name: str
    task_id: str
    success: bool
    retry: bool = False


_NON_EFFECTIVE_STATES = frozenset(
    {
        AutomationState.BOOT,
        AutomationState.PAUSED,
        AutomationState.BLOCKED_STORAGE,
        AutomationState.RECOVER,
        AutomationState.EMERGENCY_STOP,
        AutomationState.STOPPED,
    }
)


class RuntimeMetrics:
    """Monotonic runtime accounting, rate windows, and acceptance persistence."""

    def __init__(
        self,
        *,
        device_id: str,
        store: AcceptanceStore,
        clock: Callable[[], float] = time.monotonic,
        lease_clock: Callable[[], float] = time.time,
        persist_interval_seconds: float = 30.0,
        event_retention_seconds: float = FAILURE_WINDOW_SECONDS,
        lease_seconds: float = 120.0,
        owner_id: str | None = None,
    ) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        if persist_interval_seconds <= 0:
            raise ValueError("persist_interval_seconds must be > 0")
        if event_retention_seconds < FAILURE_WINDOW_SECONDS:
            raise ValueError(
                f"event_retention_seconds must be >= {FAILURE_WINDOW_SECONDS:.0f}"
            )
        if lease_seconds <= persist_interval_seconds:
            raise ValueError("lease_seconds must be > persist_interval_seconds")

        self.device_id = device_id
        self.store = store
        self.clock = clock
        self.lease_clock = lease_clock
        self.persist_interval_seconds = persist_interval_seconds
        self.event_retention_seconds = event_retention_seconds
        self.lease_seconds = lease_seconds
        self.owner_id = owner_id or str(uuid.uuid4())

        self._lock = RLock()
        self._closed = False
        self.store.acquire_metrics_lease(
            device_id,
            self.owner_id,
            lease_seconds=lease_seconds,
            now_epoch=self.lease_clock(),
        )
        self._tracker = store.load_acceptance(device_id)
        self._events: deque[ActionSample] = deque()
        self._cooldowns: dict[str, float] = {}
        self._last_tick_at: float | None = None
        self._last_state: AutomationState | None = None
        self._last_persist_at = self.clock()

    @property
    def tracker(self) -> V0AcceptanceTracker:
        return self._tracker

    def tick(self, state: AutomationState) -> None:
        """Advance monotonic runtime counters based on the previous state."""
        with self._lock:
            self._ensure_open()
            now = self.clock()
            if self._last_tick_at is not None and self._last_state is not None:
                delta = max(0.0, now - self._last_tick_at)
                if self._last_state not in _NON_EFFECTIVE_STATES:
                    self._tracker.effective_runtime_seconds += delta
                if self._last_state is AutomationState.WAIT_SESSION_CAP:
                    self._tracker.wait_session_cap_seconds += delta
                if self._last_state is AutomationState.BLOCKED_STORAGE:
                    self._tracker.blocked_storage_seconds += delta

            self._last_tick_at = now
            self._last_state = state
            self._prune_events(now)
            self._maybe_persist(now)

    def record_action(
        self,
        action_name: str,
        *,
        task_id: str = "",
        success: bool,
        retry: bool | None = None,
    ) -> None:
        if not action_name.strip():
            raise ValueError("action_name is required")
        with self._lock:
            self._ensure_open()
            now = self.clock()
            if retry is None:
                retry = self._is_retry_locked(task_id, action_name)
            self._events.append(ActionSample(now, action_name, task_id, success, retry))
            self._prune_events(now)
            self._maybe_persist(now)

    def is_retry(self, task_id: str, action_name: str) -> bool:
        if not action_name.strip():
            raise ValueError("action_name is required")
        with self._lock:
            self._ensure_open()
            self._prune_events(self.clock())
            return self._is_retry_locked(task_id, action_name)

    def rate_window(self, action_name: str, *, task_id: str = "") -> RateWindow:
        if not action_name.strip():
            raise ValueError("action_name is required")
        with self._lock:
            self._ensure_open()
            now = self.clock()
            self._prune_events(now)
            events = tuple(self._events)
            recent_minute = now - RATE_WINDOW_SECONDS
            recent_five_minutes = now - FAILURE_WINDOW_SECONDS

            actions_last_minute = sum(
                1 for sample in events if sample.timestamp >= recent_minute
            )
            five_minute_events = tuple(
                sample for sample in events if sample.timestamp >= recent_five_minutes
            )
            samples_5m = len(five_minute_events)
            failures_5m = sum(1 for sample in five_minute_events if not sample.success)
            retries_for_action = self._consecutive_retries(events, action_name, task_id)
            cooldown_until = self._cooldowns.get(action_name, 0.0)

            return RateWindow(
                actions_last_minute=actions_last_minute,
                retries_for_action=retries_for_action,
                same_action_cooling_down=now < cooldown_until,
                samples_5m=samples_5m,
                failures_5m=failures_5m,
            )

    def start_action_cooldown(self, action_name: str, seconds: float) -> None:
        if not action_name.strip():
            raise ValueError("action_name is required")
        if seconds <= 0:
            raise ValueError("seconds must be > 0")
        with self._lock:
            self._ensure_open()
            self._cooldowns[action_name] = self.clock() + seconds

    def record_collect_success(self, count: int = 1) -> None:
        self._increment_tracker("collect_success", count)

    def record_produce_success(self, count: int = 1) -> None:
        self._increment_tracker("produce_success", count)

    def record_transition(self, before: FactoryState, after: FactoryState) -> None:
        with self._lock:
            self._ensure_open()
            self._tracker.record_transition(before, after)
            self._maybe_persist(self.clock())

    def mark_blocked_storage_detected(self) -> None:
        with self._lock:
            self._ensure_open()
            self._tracker.blocked_storage_detected = True
            self._maybe_persist(self.clock())

    def mark_manual_clear_recovered(self) -> None:
        with self._lock:
            self._ensure_open()
            self._tracker.manual_clear_recovered = True
            self._maybe_persist(self.clock())

    def flush(self) -> None:
        with self._lock:
            self._ensure_open()
            self._persist(self.clock())

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            try:
                self._persist(self.clock())
            finally:
                self.store.release_metrics_lease(self.device_id, self.owner_id)
                self._closed = True

    def __enter__(self) -> RuntimeMetrics:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _increment_tracker(self, field_name: str, count: int) -> None:
        if count <= 0:
            raise ValueError("count must be positive")
        with self._lock:
            self._ensure_open()
            current = getattr(self._tracker, field_name)
            setattr(self._tracker, field_name, current + count)
            self._maybe_persist(self.clock())

    def _prune_events(self, now: float) -> None:
        cutoff = now - self.event_retention_seconds
        while self._events and self._events[0].timestamp < cutoff:
            self._events.popleft()

        expired = [name for name, until in self._cooldowns.items() if until <= now]
        for name in expired:
            self._cooldowns.pop(name, None)

    def _maybe_persist(self, now: float) -> None:
        if now - self._last_persist_at < self.persist_interval_seconds:
            return
        self._persist(now)

    def _persist(self, monotonic_now: float) -> None:
        self.store.renew_metrics_lease(
            self.device_id,
            self.owner_id,
            lease_seconds=self.lease_seconds,
            now_epoch=self.lease_clock(),
        )
        self.store.save_acceptance(self.device_id, self._tracker)
        self._last_persist_at = monotonic_now

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("RuntimeMetrics is closed")

    def _is_retry_locked(self, task_id: str, action_name: str) -> bool:
        for sample in reversed(self._events):
            if not self._same_action(sample, action_name, task_id):
                continue
            return not sample.success
        return False

    @classmethod
    def _consecutive_retries(
        cls,
        events: tuple[ActionSample, ...],
        action_name: str,
        task_id: str,
    ) -> int:
        count = 0
        for sample in reversed(events):
            if not cls._same_action(sample, action_name, task_id):
                continue
            if sample.success:
                break
            if sample.retry:
                count += 1
        return count

    @staticmethod
    def _same_action(sample: ActionSample, action_name: str, task_id: str) -> bool:
        if sample.action_name != action_name:
            return False
        return not task_id or sample.task_id == task_id
