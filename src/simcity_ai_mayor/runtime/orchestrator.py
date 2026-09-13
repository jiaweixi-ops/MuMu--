from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    RiskLevel,
    ScreenType,
)
from simcity_ai_mayor.device.command_queue import QueueBoundAdbWriter
from simcity_ai_mayor.executor.keeper import ActionRequest, Decision, Keeper, KeeperDecision
from simcity_ai_mayor.runtime.emergency_stop import EmergencyStop, EmergencyStopLatched
from simcity_ai_mayor.runtime.metrics import RuntimeMetrics
from simcity_ai_mayor.verifier.predicates import CheckResult, DiffPredicate, Verdict

MANUAL_CLEAR_MIN_CONFIDENCE = 0.99


class AcceptanceEvent(StrEnum):
    NONE = "NONE"
    COLLECT = "COLLECT"
    PRODUCE = "PRODUCE"


class CycleStatus(StrEnum):
    NO_ACTION = "NO_ACTION"
    DENIED = "DENIED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    VERIFIED = "VERIFIED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    EXECUTION_FAILED = "EXECUTION_FAILED"
    EMERGENCY_STOP = "EMERGENCY_STOP"


@dataclass(frozen=True, slots=True)
class Observation:
    device_id: str
    screen: ScreenType
    automation_state: AutomationState
    factory_state: FactoryState = FactoryState.UNKNOWN
    state: Mapping[str, Any] = field(default_factory=dict)


class Observer(Protocol):
    def observe(self) -> Observation:
        """Return a fresh observation. Implementations must not reuse stale frames."""


ActionExecutor = Callable[[QueueBoundAdbWriter], Future[Any]]


@dataclass(frozen=True, slots=True)
class PlannedAction:
    task_id: str
    name: str
    risk: RiskLevel
    execute: ActionExecutor
    verify: DiffPredicate
    requires_premium_currency: bool = False
    human_approved: bool = False
    retry: bool = False
    acceptance_event: AcceptanceEvent = AcceptanceEvent.NONE


class Planner(Protocol):
    def plan(self, observation: Observation) -> PlannedAction | None: ...


@dataclass(frozen=True, slots=True)
class CycleResult:
    status: CycleStatus
    before: Observation | None = None
    after: Observation | None = None
    keeper_decision: KeeperDecision | None = None
    verification: CheckResult | None = None
    error: str | None = None


class V0Orchestrator:
    """Minimal Observe -> Plan -> Keeper -> Execute -> Fresh Verify V0 loop."""

    def __init__(
        self,
        *,
        device_id: str,
        observer: Observer,
        planner: Planner,
        keeper: Keeper,
        writer: QueueBoundAdbWriter,
        metrics: RuntimeMetrics,
        emergency_stop: EmergencyStop | None = None,
        action_timeout_seconds: float = 15.0,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        if action_timeout_seconds <= 0:
            raise ValueError("action_timeout_seconds must be > 0")
        for component_name, component_device_id in (
            ("keeper", keeper.device_id),
            ("writer", writer.device_id),
            ("metrics", metrics.device_id),
        ):
            if component_device_id != device_id:
                raise ValueError(
                    f"{component_name} device_id {component_device_id!r} "
                    f"!= orchestrator device_id {device_id!r}"
                )

        self.device_id = device_id
        self.observer = observer
        self.planner = planner
        self.keeper = keeper
        self.writer = writer
        self.metrics = metrics
        self.emergency_stop = emergency_stop
        self.action_timeout_seconds = action_timeout_seconds
        self.sleeper = sleeper
        self._blocked_storage_recovery_pending = False

    def run_once(self) -> CycleResult:
        stopped = self._check_emergency_stop()
        if stopped is not None:
            return stopped

        self.metrics.tick(AutomationState.OBSERVE)
        before = self.observer.observe()
        self._validate_observation(before)
        self._record_special_state(before)

        self.metrics.tick(AutomationState.PLAN)
        action = self.planner.plan(before)
        if action is None:
            self.metrics.tick(before.automation_state)
            return CycleResult(CycleStatus.NO_ACTION, before=before)

        request = ActionRequest(
            device_id=self.device_id,
            task_id=action.task_id,
            name=action.name,
            risk=action.risk,
            screen=before.screen,
            requires_premium_currency=action.requires_premium_currency,
            human_approved=action.human_approved,
        )
        decision = self.keeper.evaluate(
            request,
            recent=self.metrics.rate_window(action.name),
        )
        if decision.decision is Decision.NEEDS_HUMAN:
            self.metrics.tick(AutomationState.PAUSED)
            return CycleResult(
                CycleStatus.NEEDS_HUMAN,
                before=before,
                keeper_decision=decision,
            )
        if not decision.allowed:
            self.metrics.tick(before.automation_state)
            return CycleResult(
                CycleStatus.DENIED,
                before=before,
                keeper_decision=decision,
            )

        stopped = self._check_emergency_stop(before)
        if stopped is not None:
            return stopped

        self.metrics.tick(AutomationState.EXECUTE)
        future: Future[Any] | None = None
        try:
            future = action.execute(self.writer)
            future.result(timeout=self.action_timeout_seconds)
        except FutureTimeoutError as exc:
            if future is not None:
                future.cancel()
            self.writer.request_cancel()
            try:
                self.writer.drain()
            finally:
                self.writer.clear_cancel()
            self.metrics.record_action(action.name, success=False, retry=action.retry)
            self.metrics.tick(AutomationState.RECOVER)
            return CycleResult(
                CycleStatus.EXECUTION_FAILED,
                before=before,
                keeper_decision=decision,
                error=(
                    f"{type(exc).__name__}: action timed out; "
                    "ADB writer cancelled, drained, and reopened"
                ),
            )
        except KeyboardInterrupt as exc:
            return self._handle_keyboard_interrupt(
                exc,
                before=before,
                keeper_decision=decision,
            )
        except Exception as exc:
            self.metrics.record_action(action.name, success=False, retry=action.retry)
            self.metrics.tick(AutomationState.RECOVER)
            return CycleResult(
                CycleStatus.EXECUTION_FAILED,
                before=before,
                keeper_decision=decision,
                error=f"{type(exc).__name__}: {exc}",
            )

        self.metrics.tick(AutomationState.VERIFY)
        after = self.observer.observe()
        self._validate_observation(after)
        self._record_special_state(after)
        verification = action.verify(before.state, after.state)
        success = verification.verdict is Verdict.PASS
        self.metrics.record_action(action.name, success=success, retry=action.retry)

        if not success:
            self.metrics.tick(AutomationState.RECOVER)
            return CycleResult(
                CycleStatus.VERIFICATION_FAILED,
                before=before,
                after=after,
                keeper_decision=decision,
                verification=verification,
            )

        self._record_acceptance_success(action, before, after)
        self.metrics.tick(after.automation_state)
        return CycleResult(
            CycleStatus.VERIFIED,
            before=before,
            after=after,
            keeper_decision=decision,
            verification=verification,
        )

    def run(
        self,
        *,
        max_cycles: int | None = None,
        idle_sleep_seconds: float = 0.5,
    ) -> CycleResult | None:
        if max_cycles is not None and max_cycles <= 0:
            raise ValueError("max_cycles must be > 0 when provided")
        if idle_sleep_seconds < 0:
            raise ValueError("idle_sleep_seconds must be >= 0")

        last_result: CycleResult | None = None
        completed = 0
        stop_statuses = {CycleStatus.EMERGENCY_STOP, CycleStatus.NEEDS_HUMAN}
        while max_cycles is None or completed < max_cycles:
            try:
                last_result = self.run_once()
                completed += 1
                if last_result.status in stop_statuses:
                    break
                if idle_sleep_seconds:
                    self.sleeper(idle_sleep_seconds)
            except KeyboardInterrupt as exc:
                last_result = self._handle_keyboard_interrupt(exc)
                break
        return last_result

    def _check_emergency_stop(
        self,
        before: Observation | None = None,
    ) -> CycleResult | None:
        if self.emergency_stop is None:
            return None
        try:
            self.emergency_stop.check()
        except EmergencyStopLatched as exc:
            self.writer.request_cancel()
            self.metrics.tick(AutomationState.EMERGENCY_STOP)
            return CycleResult(
                CycleStatus.EMERGENCY_STOP,
                before=before,
                error=str(exc),
            )
        return None

    def _handle_keyboard_interrupt(
        self,
        exc: KeyboardInterrupt,
        *,
        before: Observation | None = None,
        keeper_decision: KeeperDecision | None = None,
    ) -> CycleResult:
        self.writer.request_cancel()
        self.writer.drain()
        self.metrics.tick(AutomationState.EMERGENCY_STOP)
        self.metrics.flush()
        return CycleResult(
            CycleStatus.EMERGENCY_STOP,
            before=before,
            keeper_decision=keeper_decision,
            error=f"{type(exc).__name__}: user interrupt; ADB writer remains cancelled",
        )

    def _validate_observation(self, observation: Observation) -> None:
        if observation.device_id != self.device_id:
            raise ValueError(
                f"observer device_id {observation.device_id!r} "
                f"!= orchestrator device_id {self.device_id!r}"
            )

    def _record_special_state(self, observation: Observation) -> None:
        if observation.automation_state is AutomationState.BLOCKED_STORAGE:
            self.metrics.mark_blocked_storage_detected()
            self._blocked_storage_recovery_pending = True
            return
        if (
            self._blocked_storage_recovery_pending
            and self._has_trusted_free_storage(observation)
        ):
            self.metrics.mark_manual_clear_recovered()
            self._blocked_storage_recovery_pending = False

    @staticmethod
    def _has_trusted_free_storage(observation: Observation) -> bool:
        try:
            used = int(observation.state["storage_used"])
            capacity = int(observation.state["storage_capacity"])
            confidence = float(observation.state["storage_confidence"])
        except (KeyError, TypeError, ValueError):
            return False
        return (
            capacity > 0
            and 0 <= used < capacity
            and confidence >= MANUAL_CLEAR_MIN_CONFIDENCE
        )

    def _record_acceptance_success(
        self,
        action: PlannedAction,
        before: Observation,
        after: Observation,
    ) -> None:
        if action.acceptance_event is AcceptanceEvent.COLLECT:
            self.metrics.record_collect_success()
        elif action.acceptance_event is AcceptanceEvent.PRODUCE:
            self.metrics.record_produce_success()

        if (
            before.factory_state is not FactoryState.UNKNOWN
            and after.factory_state is not FactoryState.UNKNOWN
            and before.factory_state is not after.factory_state
        ):
            self.metrics.record_transition(before.factory_state, after.factory_state)
