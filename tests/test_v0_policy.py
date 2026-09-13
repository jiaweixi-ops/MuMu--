from simcity_ai_mayor.core.models import AutomationState, StorageCapacity, V0Policy
from simcity_ai_mayor.core.v0_policy import (
    can_collect_one,
    can_start_production,
    validate_v0_test_precondition,
)


def test_collect_one_requires_margin() -> None:
    policy = V0Policy(collect_safety_margin=2)
    assert not can_collect_one(
        StorageCapacity(used=118, capacity=120, confidence=1.0),
        policy,
    ).allowed
    assert can_collect_one(
        StorageCapacity(used=117, capacity=120, confidence=1.0),
        policy,
    ).allowed


def test_session_cap_takes_precedence_over_storage_reserve() -> None:
    policy = V0Policy(max_total_session_output=12)
    decision = can_start_production(
        StorageCapacity(used=119, capacity=120, confidence=1.0),
        policy,
        session_total_output=12,
        item_session_output=0,
    )
    assert decision.state is AutomationState.WAIT_SESSION_CAP


def test_untrusted_ocr_has_own_wait_state() -> None:
    decision = can_start_production(
        StorageCapacity(used=80, capacity=120, confidence=0.98),
        V0Policy(),
        session_total_output=0,
        item_session_output=0,
    )
    assert decision.state is AutomationState.WAIT_OCR_UNTRUSTED


def test_v0_acceptance_precondition_requires_headroom_for_test_budget() -> None:
    policy = V0Policy(
        reserve_free_ratio=0.10,
        reserve_free_min=10,
        max_total_session_output=12,
    )
    ok, _ = validate_v0_test_precondition(
        StorageCapacity(used=90, capacity=120, confidence=1.0),
        policy,
    )
    assert ok

    ok, reason = validate_v0_test_precondition(
        StorageCapacity(used=100, capacity=120, confidence=1.0),
        policy,
    )
    assert not ok
    assert "required" in reason


def test_reserve_free_uses_decimal_configuration_intent() -> None:
    assert V0Policy(reserve_free_ratio=0.20).reserve_free(120) == 24
    assert V0Policy(reserve_free_ratio=0.28, reserve_free_min=0).reserve_free(25) == 7


def test_storage_confidence_defaults_to_untrusted() -> None:
    storage = StorageCapacity(used=10, capacity=120)
    assert storage.confidence == 0.0
    assert not can_collect_one(storage, V0Policy()).allowed
