from __future__ import annotations

from dataclasses import dataclass, field

from simcity_ai_mayor.core.models import FactoryState


REQUIRED_TRANSITION_PAIRS: tuple[tuple[FactoryState, FactoryState], ...] = (
    (FactoryState.IDLE, FactoryState.PRODUCING),
    (FactoryState.PRODUCING, FactoryState.COMPLETED_COLLECTABLE),
    (FactoryState.COMPLETED_COLLECTABLE, FactoryState.COLLECTED),
    (FactoryState.COLLECTED, FactoryState.IDLE),
)


def transition_key(before: FactoryState, after: FactoryState) -> str:
    return f"{before.value}->{after.value}"


REQUIRED_TRANSITIONS = tuple(
    transition_key(before, after) for before, after in REQUIRED_TRANSITION_PAIRS
)


@dataclass(slots=True)
class V0AcceptanceTracker:
    collect_success: int = 0
    produce_success: int = 0
    unsafe_purchase: int = 0
    unsafe_sale: int = 0
    premium_spend: int = 0
    infinite_loop_incidents: int = 0
    stale_state_actions: int = 0
    adb_write_order_incidents: int = 0
    effective_runtime_seconds: float = 0.0
    wait_session_cap_seconds: float = 0.0
    blocked_storage_seconds: float = 0.0
    blocked_storage_detected: bool = False
    manual_clear_recovered: bool = False
    transitions: dict[str, int] = field(default_factory=dict)

    def record_transition(self, before: FactoryState, after: FactoryState) -> None:
        key = transition_key(before, after)
        self.transitions[key] = self.transitions.get(key, 0) + 1


@dataclass(frozen=True, slots=True)
class V0AcceptanceRequirements:
    min_effective_runtime_seconds: float = 4 * 3600
    min_collect_success: int = 10
    min_produce_success: int = 10
    min_each_required_transition: int = 3
    require_blocked_storage_test: bool = True
    require_manual_clear_recovery: bool = True


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    passed: bool
    failures: tuple[str, ...]


def evaluate_v0_acceptance(
    tracker: V0AcceptanceTracker,
    requirements: V0AcceptanceRequirements | None = None,
) -> AcceptanceResult:
    requirements = requirements or V0AcceptanceRequirements()
    failures: list[str] = []

    if tracker.effective_runtime_seconds < requirements.min_effective_runtime_seconds:
        failures.append(
            "effective runtime "
            f"{tracker.effective_runtime_seconds:.0f}s < "
            f"{requirements.min_effective_runtime_seconds:.0f}s"
        )
    if tracker.collect_success < requirements.min_collect_success:
        failures.append(
            f"collect coverage {tracker.collect_success} < {requirements.min_collect_success}"
        )
    if tracker.produce_success < requirements.min_produce_success:
        failures.append(
            f"production coverage {tracker.produce_success} < "
            f"{requirements.min_produce_success}"
        )

    for transition in REQUIRED_TRANSITIONS:
        count = tracker.transitions.get(transition, 0)
        if count < requirements.min_each_required_transition:
            failures.append(
                f"transition {transition} coverage {count} < "
                f"{requirements.min_each_required_transition}"
            )

    if requirements.require_blocked_storage_test and not tracker.blocked_storage_detected:
        failures.append("BLOCKED_STORAGE test was not observed")
    if requirements.require_manual_clear_recovery and not tracker.manual_clear_recovered:
        failures.append("manual-clear recovery test did not pass")

    zero_tolerance = {
        "unsafe_purchase": tracker.unsafe_purchase,
        "unsafe_sale": tracker.unsafe_sale,
        "premium_spend": tracker.premium_spend,
        "infinite_loop_incidents": tracker.infinite_loop_incidents,
        "stale_state_actions": tracker.stale_state_actions,
        "adb_write_order_incidents": tracker.adb_write_order_incidents,
    }
    for name, value in zero_tolerance.items():
        if value != 0:
            failures.append(f"{name} must be 0, got {value}")

    return AcceptanceResult(not failures, tuple(failures))
