from simcity_ai_mayor.core.acceptance import (
    REQUIRED_TRANSITION_PAIRS,
    V0AcceptanceTracker,
    evaluate_v0_acceptance,
)
from simcity_ai_mayor.core.models import FactoryState


def passing_tracker() -> V0AcceptanceTracker:
    tracker = V0AcceptanceTracker(
        collect_success=10,
        produce_success=10,
        effective_runtime_seconds=4 * 3600,
        blocked_storage_detected=True,
        manual_clear_recovered=True,
    )
    for before, after in REQUIRED_TRANSITION_PAIRS:
        for _ in range(3):
            tracker.record_transition(before, after)
    return tracker


def test_empty_run_cannot_pass_v0_acceptance() -> None:
    result = evaluate_v0_acceptance(V0AcceptanceTracker())
    assert not result.passed
    assert any("effective runtime" in failure for failure in result.failures)
    assert any("collect coverage" in failure for failure in result.failures)


def test_activity_duration_and_recovery_coverage_can_pass() -> None:
    result = evaluate_v0_acceptance(passing_tracker())
    assert result.passed
    assert result.failures == ()


def test_ten_minute_run_cannot_pass_four_hour_gate() -> None:
    tracker = passing_tracker()
    tracker.effective_runtime_seconds = 10 * 60
    result = evaluate_v0_acceptance(tracker)
    assert not result.passed
    assert any("effective runtime" in failure for failure in result.failures)


def test_transition_api_uses_factory_enum() -> None:
    tracker = V0AcceptanceTracker()
    tracker.record_transition(FactoryState.IDLE, FactoryState.PRODUCING)
    assert tracker.transitions["IDLE->PRODUCING"] == 1


def test_any_unsafe_action_fails_gate() -> None:
    tracker = passing_tracker()
    tracker.unsafe_sale = 1
    result = evaluate_v0_acceptance(tracker)
    assert not result.passed
    assert "unsafe_sale must be 0, got 1" in result.failures
