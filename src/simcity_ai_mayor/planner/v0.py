from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    RiskLevel,
    ScreenType,
    V0Policy,
)
from simcity_ai_mayor.core.v0_policy import can_collect_one, can_start_production
from simcity_ai_mayor.runtime.orchestrator import (
    AcceptanceEvent,
    Observation,
    PlannedAction,
    PlanningResult,
)
from simcity_ai_mayor.verifier.predicates import Verdict, state_transition, storage_delta


class SessionOutputStore(Protocol):
    def total_output(self, device_id: str) -> int: ...

    def item_output(self, device_id: str, item: str) -> int: ...

    def record_output(self, device_id: str, item: str, count: int = 1) -> None: ...

    def release_output(self, device_id: str, item: str, count: int = 1) -> None: ...


class StorageTransitionGate(Protocol):
    def expect_delta(self, minimum: int, maximum: int) -> None: ...


@dataclass(frozen=True, slots=True)
class TapPoint:
    x: int
    y: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise ValueError("tap coordinates must be >= 0")


@dataclass(frozen=True, slots=True)
class ProductionRecipe:
    item: str
    tap: TapPoint

    def __post_init__(self) -> None:
        if not self.item.strip():
            raise ValueError("production item is required")


class V0Planner:
    """Minimal navigation + factory planner for the trusted V0 loop."""

    def __init__(
        self,
        *,
        device_id: str,
        session_store: SessionOutputStore,
        policy: V0Policy | None = None,
        enter_factory_tap: TapPoint | None = None,
        collect_tap: TapPoint | None = None,
        production: ProductionRecipe | None = None,
        storage_gate: StorageTransitionGate | None = None,
    ) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        self.device_id = device_id
        self.session_store = session_store
        self.policy = policy or V0Policy()
        self.enter_factory_tap = enter_factory_tap
        self.collect_tap = collect_tap
        self.production = production
        self.storage_gate = storage_gate

    def plan(self, observation: Observation) -> PlannedAction | PlanningResult | None:
        if observation.device_id != self.device_id:
            raise ValueError(
                f"observation device {observation.device_id!r} != planner device "
                f"{self.device_id!r}"
            )
        if observation.screen is ScreenType.CITY:
            return self._plan_enter_factory()
        if observation.screen is not ScreenType.FACTORY:
            return None
        if observation.storage is None:
            return PlanningResult(
                action=None,
                state=AutomationState.WAIT_OCR_UNTRUSTED,
                reason="factory storage capacity is unavailable",
            )

        if observation.factory_state is FactoryState.COMPLETED_STORAGE_BLOCKED:
            return PlanningResult(
                action=None,
                state=AutomationState.BLOCKED_STORAGE,
                reason="factory collection is blocked by storage",
            )
        if observation.factory_state is FactoryState.COMPLETED_COLLECTABLE:
            return self._plan_collect(observation)
        if observation.factory_state is FactoryState.IDLE:
            return self._plan_production(observation)
        return None

    def _plan_enter_factory(self) -> PlannedAction | PlanningResult:
        if self.enter_factory_tap is None:
            return PlanningResult(
                None,
                AutomationState.RECOVER,
                "CITY detected but enter_factory_tap is not configured",
            )
        point = self.enter_factory_tap
        return PlannedAction(
            task_id="v0:enter_factory",
            name="enter_factory",
            risk=RiskLevel.L0,
            execute=lambda writer: writer.tap(point.x, point.y),
            verify=state_transition(
                "screen",
                ScreenType.CITY.value,
                ScreenType.FACTORY.value,
            ),
        )

    def _plan_collect(self, observation: Observation) -> PlannedAction | PlanningResult:
        assert observation.storage is not None
        decision = can_collect_one(observation.storage, self.policy)
        if not decision.allowed:
            state = (
                AutomationState.WAIT_OCR_UNTRUSTED
                if observation.storage.confidence < self.policy.min_ocr_confidence
                else AutomationState.WAIT_STORAGE_RESERVE
            )
            return PlanningResult(None, state, decision.reason)
        if self.collect_tap is None:
            return PlanningResult(
                None,
                AutomationState.RECOVER,
                "collect tap is not configured",
            )

        point = self.collect_tap

        def execute(writer):
            if self.storage_gate is not None:
                self.storage_gate.expect_delta(1, 1)
            return writer.tap(point.x, point.y)

        return PlannedAction(
            task_id="v0:collect_factory",
            name="collect_factory",
            risk=RiskLevel.L0,
            execute=execute,
            verify=storage_delta(1, 1),
            acceptance_event=AcceptanceEvent.COLLECT,
        )

    def _plan_production(self, observation: Observation) -> PlannedAction | PlanningResult | None:
        if self.production is None:
            return None
        assert observation.storage is not None
        recipe = self.production
        total_output = self.session_store.total_output(self.device_id)
        item_output = self.session_store.item_output(self.device_id, recipe.item)
        decision = can_start_production(
            observation.storage,
            self.policy,
            session_total_output=total_output,
            item_session_output=item_output,
        )
        if not decision.allowed:
            return PlanningResult(None, decision.state, decision.reason)

        def execute(writer):
            # Reserve before the device write so crashes/ambiguous timeouts can never
            # under-count production and bypass persistent session caps.
            self.session_store.record_output(self.device_id, recipe.item, 1)
            return writer.tap(recipe.tap.x, recipe.tap.y)

        production_transition = state_transition(
            "factory_state",
            FactoryState.IDLE.value,
            FactoryState.PRODUCING.value,
        )

        def verify(before, after):
            result = production_transition(before, after)
            if result.verdict is Verdict.FAIL:
                # A trustworthy fresh frame explicitly proved the production did not
                # start, so the conservative pre-reservation can safely be released.
                self.session_store.release_output(self.device_id, recipe.item, 1)
            return result

        return PlannedAction(
            task_id=f"v0:produce:{recipe.item}",
            name=f"produce_{recipe.item}",
            risk=RiskLevel.L0,
            execute=execute,
            verify=verify,
            acceptance_event=AcceptanceEvent.PRODUCE,
        )
