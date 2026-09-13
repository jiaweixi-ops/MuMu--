from __future__ import annotations

from simcity_ai_mayor.core.models import (
    AutomationState,
    CollectDecision,
    ProductionDecision,
    StorageCapacity,
    V0Policy,
)


def can_collect_one(storage: StorageCapacity, policy: V0Policy) -> CollectDecision:
    """Return whether V0 may collect exactly one item.

    V0 intentionally avoids batch-collection dependency. A single item can only be
    collected when there is one slot plus the configured safety margin available.
    """

    required_free = 1 + policy.collect_safety_margin
    if storage.confidence < 0.99:
        return CollectDecision(False, "storage OCR confidence below 0.99")
    if storage.free < required_free:
        return CollectDecision(
            False,
            f"insufficient storage: free={storage.free}, required={required_free}",
        )
    return CollectDecision(True, "single-item collection is within storage guard")


def can_start_production(
    storage: StorageCapacity,
    policy: V0Policy,
    *,
    session_total_output: int,
    item_session_output: int,
) -> ProductionDecision:
    """Evaluate V0 production guard without pretending to know item inventory."""

    if storage.confidence < 0.99:
        return ProductionDecision(
            False,
            AutomationState.WAIT_STORAGE_RESERVE,
            "storage OCR confidence below 0.99",
        )

    reserve = policy.reserve_free(storage.capacity)
    if storage.free <= reserve:
        return ProductionDecision(
            False,
            AutomationState.WAIT_STORAGE_RESERVE,
            f"free={storage.free} <= reserve={reserve}",
        )

    if session_total_output >= policy.max_total_session_output:
        return ProductionDecision(
            False,
            AutomationState.WAIT_SESSION_CAP,
            "total V0 session output cap reached",
        )

    if item_session_output >= policy.max_item_session_output:
        return ProductionDecision(
            False,
            AutomationState.WAIT_SESSION_CAP,
            "item V0 session output cap reached",
        )

    return ProductionDecision(True, AutomationState.EXECUTE, "production allowed")


def validate_v0_test_precondition(storage: StorageCapacity, policy: V0Policy) -> tuple[bool, str]:
    """Ensure the acceptance run starts with enough free storage to exercise V0."""

    required = policy.reserve_free(storage.capacity) + policy.max_total_session_output
    if storage.free < required:
        return (
            False,
            f"V0 acceptance precondition failed: free={storage.free}, required>={required}",
        )
    return True, "V0 acceptance storage precondition satisfied"
