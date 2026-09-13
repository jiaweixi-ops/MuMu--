from simcity_ai_mayor.core.acceptance import (
    REQUIRED_TRANSITIONS,
    V0AcceptanceTracker,
    evaluate_v0_acceptance,
)


def test_empty_run_cannot_pass_v0_acceptance() -> None:
    result = evaluate_v0_acceptance(V0AcceptanceTracker())
    assert not result.passed
    assert any("collect coverage" in failure for failure in result.failures)
    assert any("production coverage" in failure for failure in result.failures)


def test_activity_coverage_and_zero_tolerance_can_pass() -> None:
    tracker = V0AcceptanceTracker(collect_success=10, produce_success=10)
    for transition in REQUIRED_TRANSITIONS:
        tracker.transitions[transition] = 3

    result = evaluate_v0_acceptance(tracker)
    assert result.passed
    assert result.failures == ()


def test_any_unsafe_action_fails_gate() -> None:
    tracker = V0AcceptanceTracker(collect_success=10, produce_success=10, unsafe_sale=1)
    for transition in REQUIRED_TRANSITIONS:
        tracker.transitions[transition] = 3

    result = evaluate_v0_acceptance(tracker)
    assert not result.passed
    assert "unsafe_sale must be 0, got 1" in result.failures
