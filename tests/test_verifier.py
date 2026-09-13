from simcity_ai_mayor.verifier.predicates import (
    CheckResult,
    Verdict,
    all_diff,
    all_of,
    counter_delta,
    screen_eq,
    state_changed,
    state_eq,
    verify_required,
)


def test_unknown_is_preserved_and_not_success() -> None:
    result = screen_eq("CITY")({})
    assert result.verdict is Verdict.UNKNOWN
    collapsed = verify_required([result])
    assert collapsed.verdict is Verdict.UNKNOWN


def test_composable_state_predicates() -> None:
    predicate = all_of(
        screen_eq("CITY"),
        state_eq("factory.state", "COMPLETED_COLLECTABLE"),
    )
    state = {
        "screen": "CITY",
        "factory": {"state": "COMPLETED_COLLECTABLE"},
    }
    assert predicate(state).verdict is Verdict.PASS


def test_composable_diff_predicates() -> None:
    predicate = all_diff(
        counter_delta("storage_used", 1, 1),
        state_changed("factory.state"),
    )
    before = {"storage_used": 20, "factory": {"state": "COMPLETED_COLLECTABLE"}}
    after = {"storage_used": 21, "factory": {"state": "IDLE"}}
    assert predicate(before, after).verdict is Verdict.PASS


def test_verify_required_collects_all_failure_reasons() -> None:
    result = verify_required(
        [
            CheckResult(Verdict.FAIL, "first failure"),
            CheckResult(Verdict.UNKNOWN, "missing signal"),
            CheckResult(Verdict.FAIL, "second failure"),
        ]
    )
    assert result.verdict is Verdict.FAIL
    assert "first failure" in result.reason
    assert "second failure" in result.reason
    assert "missing signal" in result.reason
