from __future__ import annotations

from simcity_ai_mayor.core.models import (
    AutomationState,
    CollectDecision,
    ProductionDecision,
    StorageCapacity,
    V0Policy,
)


def can_collect_one(storage: StorageCapacity, policy: V0Policy) -> CollectDecision:
    """Return whether V0 may collect exactly one item."""
    required_free = 1 + policy.collect_safety_margin
    if storage.confidence < policy.min_ocr_confidence:
        return CollectDecision(
            False,
            f"storage OCR confidence {storage.confidence:.3f} "
            f"< {policy.min_ocr_confidence:.3f}",
        )
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
    if storage.confidence < policy.min_ocr_confidence:
        return ProductionDecision(
            False,
            AutomationState.WAIT_OCR_UNTRUSTED,
            f"storage OCR confidence {storage.confidence:.3f} "
            f"< {policy.min_ocr_confidence:.3f}",
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

    reserve = policy.reserve_free(storage.capacity)
    if storage.free <= reserve:
        return ProductionDecision(
            False,
            AutomationState.WAIT_STORAGE_RESERVE,
            f"free={storage.free} <= reserve={reserve}",
        )

    return ProductionDecision(True, AutomationState.EXECUTE, "production allowed")


def validate_v0_test_precondition(
    storage: StorageCapacity,
    policy: V0Policy,
) -> tuple[bool, str]:
    """Ensure the acceptance run starts with enough trusted free storage."""
    if storage.confidence < policy.min_ocr_confidence:
        return False, "V0 acceptance precondition failed: storage OCR is untrusted"
    required = policy.reserve_free(storage.capacity) + policy.max_total_session_output
    if storage.free < required:
        return (
            False,
            f"V0 acceptance precondition failed: free={storage.free}, required>={required}",
        )
    return True, "V0 acceptance storage precondition satisfied"
