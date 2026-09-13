from simcity_ai_mayor.verifier.predicates import (
    Verdict,
    all_of,
    counter_delta,
    screen_eq,
    state_eq,
    verify_required,
)


def test_unknown_is_not_success() -> None:
    result = screen_eq("CITY")({})
    assert result.verdict is Verdict.UNKNOWN
    collapsed = verify_required([result])
    assert collapsed.verdict is Verdict.FAIL


def test_composable_predicates() -> None:
    predicate = all_of(
        screen_eq("CITY"),
        state_eq("factory.state", "COMPLETED_COLLECTABLE"),
    )
    state = {
        "screen": "CITY",
        "factory": {"state": "COMPLETED_COLLECTABLE"},
    }
    assert predicate(state).verdict is Verdict.PASS


def test_counter_delta() -> None:
    check = counter_delta("storage_used", 1, 1)
    assert check({"storage_used": 20}, {"storage_used": 21}).verdict is Verdict.PASS
    assert check({"storage_used": 20}, {"storage_used": 23}).verdict is Verdict.FAIL
