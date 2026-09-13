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
from simcity_ai_mayor.verifier.predicates import state_transition, storage_delta


class SessionOutputStore(Protocol):
    def total_output(self, device_id: str) -> int: ...

    def item_output(self, device_id: str, item: str) -> int: ...

    def record_output(self, device_id: str, item: str, count: int = 1) -> None: ...


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
    """Minimal factory planner: collect one item or start one configured recipe."""

    def __init__(
        self,
        *,
        device_id: str,
        session_store: SessionOutputStore,
        policy: V0Policy | None = None,
        collect_tap: TapPoint | None = None,
        production: ProductionRecipe | None = None,
    ) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        self.device_id = device_id
        self.session_store = session_store
        self.policy = policy or V0Policy()
        self.collect_tap = collect_tap
        self.production = production

    def plan(self, observation: Observation) -> PlannedAction | PlanningResult | None:
        if observation.device_id != self.device_id:
            raise ValueError(
                f"observation device {observation.device_id!r} != planner device "
                f"{self.device_id!r}"
            )
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
        return PlannedAction(
            task_id="v0:collect_factory",
            name="collect_factory",
            risk=RiskLevel.L0,
            execute=lambda writer: writer.tap(point.x, point.y),
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
            # Debit the cap before sending the tap. If the ADB tap fails, over-counting is
            # conservative and cannot be used to bypass the persistent session cap.
            self.session_store.record_output(self.device_id, recipe.item, 1)
            return writer.tap(recipe.tap.x, recipe.tap.y)

        return PlannedAction(
            task_id=f"v0:produce:{recipe.item}",
            name=f"produce_{recipe.item}",
            risk=RiskLevel.L0,
            execute=execute,
            verify=state_transition(
                "factory_state",
                FactoryState.IDLE.value,
                FactoryState.PRODUCING.value,
            ),
            acceptance_event=AcceptanceEvent.PRODUCE,
        )
