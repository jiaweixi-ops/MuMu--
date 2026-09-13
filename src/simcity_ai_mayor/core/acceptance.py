from __future__ import annotations

from dataclasses import dataclass, field


REQUIRED_TRANSITIONS = (
    "IDLE->PRODUCING",
    "PRODUCING->COMPLETED_COLLECTABLE",
    "COMPLETED_COLLECTABLE->COLLECTED",
    "COLLECTED->IDLE",
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
    transitions: dict[str, int] = field(default_factory=dict)

    def record_transition(self, before: str, after: str) -> None:
        key = f"{before}->{after}"
        self.transitions[key] = self.transitions.get(key, 0) + 1


@dataclass(frozen=True, slots=True)
class V0AcceptanceRequirements:
    min_collect_success: int = 10
    min_produce_success: int = 10
    min_each_required_transition: int = 3


@dataclass(frozen=True, slots=True)
class AcceptanceResult:
    passed: bool
    failures: tuple[str, ...]


def evaluate_v0_acceptance(
    tracker: V0AcceptanceTracker,
    requirements: V0AcceptanceRequirements = V0AcceptanceRequirements(),
) -> AcceptanceResult:
    failures: list[str] = []

    if tracker.collect_success < requirements.min_collect_success:
        failures.append(
            f"collect coverage {tracker.collect_success} < {requirements.min_collect_success}"
        )
    if tracker.produce_success < requirements.min_produce_success:
        failures.append(
            f"production coverage {tracker.produce_success} < {requirements.min_produce_success}"
        )

    for transition in REQUIRED_TRANSITIONS:
        count = tracker.transitions.get(transition, 0)
        if count < requirements.min_each_required_transition:
            failures.append(
                f"transition {transition} coverage {count} < "
                f"{requirements.min_each_required_transition}"
            )

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
