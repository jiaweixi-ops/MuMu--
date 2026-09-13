from simcity_ai_mayor.core.models import AutomationState, StorageCapacity, V0Policy
from simcity_ai_mayor.core.v0_policy import (
    can_collect_one,
    can_start_production,
    validate_v0_test_precondition,
)


def test_collect_one_requires_margin() -> None:
    policy = V0Policy(collect_safety_margin=2)
    assert not can_collect_one(StorageCapacity(used=118, capacity=120), policy).allowed
    assert can_collect_one(StorageCapacity(used=117, capacity=120), policy).allowed


def test_storage_reserve_and_session_cap_are_distinct_states() -> None:
    policy = V0Policy(
        reserve_free_ratio=0.10,
        reserve_free_min=10,
        max_total_session_output=12,
        max_item_session_output=8,
    )

    storage_limited = can_start_production(
        StorageCapacity(used=110, capacity=120),
        policy,
        session_total_output=0,
        item_session_output=0,
    )
    assert not storage_limited.allowed
    assert storage_limited.state is AutomationState.WAIT_STORAGE_RESERVE

    session_limited = can_start_production(
        StorageCapacity(used=80, capacity=120),
        policy,
        session_total_output=12,
        item_session_output=6,
    )
    assert not session_limited.allowed
    assert session_limited.state is AutomationState.WAIT_SESSION_CAP


def test_v0_acceptance_precondition_requires_headroom_for_test_budget() -> None:
    policy = V0Policy(
        reserve_free_ratio=0.10,
        reserve_free_min=10,
        max_total_session_output=12,
    )
    ok, _ = validate_v0_test_precondition(StorageCapacity(used=90, capacity=120), policy)
    assert ok

    ok, reason = validate_v0_test_precondition(StorageCapacity(used=100, capacity=120), policy)
    assert not ok
    assert "required" in reason


def test_low_confidence_ocr_never_allows_collection() -> None:
    policy = V0Policy()
    decision = can_collect_one(StorageCapacity(used=10, capacity=120, confidence=0.98), policy)
    assert not decision.allowed
