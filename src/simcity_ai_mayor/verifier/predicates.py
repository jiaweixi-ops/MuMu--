from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping


__all__ = [
    "CheckResult",
    "DiffPredicate",
    "StatePredicate",
    "Verdict",
    "all_diff",
    "all_of",
    "any_diff",
    "any_of",
    "counter_delta",
    "counter_eq",
    "screen_eq",
    "screen_not_eq",
    "state_changed",
    "state_eq",
    "storage_delta",
    "verify_required",
]

# TODO(V1): visual predicates that require the vision/template layer:
# icon_present, icon_absent, frame_changed, factory_state_eq, stable_for, within.


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CheckResult:
    verdict: Verdict
    reason: str


StatePredicate = Callable[[Mapping[str, Any]], CheckResult]
DiffPredicate = Callable[[Mapping[str, Any], Mapping[str, Any]], CheckResult]


def screen_eq(expected: str) -> StatePredicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        actual = state.get("screen")
        if actual is None:
            return CheckResult(Verdict.UNKNOWN, "screen missing from state")
        if actual == expected:
            return CheckResult(Verdict.PASS, f"screen == {expected}")
        return CheckResult(Verdict.FAIL, f"screen={actual!r}, expected={expected!r}")

    return check


def screen_not_eq(unexpected: str) -> StatePredicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        actual = state.get("screen")
        if actual is None:
            return CheckResult(Verdict.UNKNOWN, "screen missing from state")
        if actual != unexpected:
            return CheckResult(Verdict.PASS, f"screen != {unexpected}")
        return CheckResult(Verdict.FAIL, f"screen unexpectedly equals {unexpected!r}")

    return check


def _lookup(state: Mapping[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = state
    for key in path.split("."):
        if not isinstance(current, Mapping) or key not in current:
            return False, None
        current = current[key]
    return True, current


def state_eq(path: str, expected: Any) -> StatePredicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        found, current = _lookup(state, path)
        if not found:
            return CheckResult(Verdict.UNKNOWN, f"state path missing: {path}")
        if current == expected:
            return CheckResult(Verdict.PASS, f"{path} == {expected!r}")
        return CheckResult(Verdict.FAIL, f"{path}={current!r}, expected={expected!r}")

    return check


def counter_eq(counter: str, expected: int) -> StatePredicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        if counter not in state:
            return CheckResult(Verdict.UNKNOWN, f"counter missing: {counter}")
        try:
            actual = int(state[counter])
        except (TypeError, ValueError):
            return CheckResult(Verdict.UNKNOWN, f"counter is not numeric: {counter}")
        if actual == expected:
            return CheckResult(Verdict.PASS, f"{counter} == {expected}")
        return CheckResult(Verdict.FAIL, f"{counter}={actual}, expected={expected}")

    return check


def counter_delta(counter: str, minimum: int, maximum: int) -> DiffPredicate:
    def check(
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> CheckResult:
        if counter not in before or counter not in after:
            return CheckResult(Verdict.UNKNOWN, f"counter missing: {counter}")
        try:
            delta = int(after[counter]) - int(before[counter])
        except (TypeError, ValueError):
            return CheckResult(Verdict.UNKNOWN, f"counter is not numeric: {counter}")
        if minimum <= delta <= maximum:
            return CheckResult(Verdict.PASS, f"{counter} delta={delta}")
        return CheckResult(
            Verdict.FAIL,
            f"{counter} delta={delta}, expected [{minimum}, {maximum}]",
        )

    return check


def storage_delta(minimum: int, maximum: int) -> DiffPredicate:
    return counter_delta("storage_used", minimum, maximum)


def state_changed(path: str) -> DiffPredicate:
    def check(
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> CheckResult:
        before_found, before_value = _lookup(before, path)
        after_found, after_value = _lookup(after, path)
        if not before_found or not after_found:
            return CheckResult(Verdict.UNKNOWN, f"state path missing: {path}")
        if before_value != after_value:
            return CheckResult(
                Verdict.PASS,
                f"{path} changed from {before_value!r} to {after_value!r}",
            )
        return CheckResult(Verdict.FAIL, f"{path} did not change")

    return check


def all_of(*predicates: StatePredicate) -> StatePredicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        return _combine_results(predicate(state) for predicate in predicates)

    return check


def any_of(*predicates: StatePredicate) -> StatePredicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        return _combine_any(predicate(state) for predicate in predicates)

    return check


def all_diff(*predicates: DiffPredicate) -> DiffPredicate:
    def check(
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> CheckResult:
        return _combine_results(predicate(before, after) for predicate in predicates)

    return check


def any_diff(*predicates: DiffPredicate) -> DiffPredicate:
    def check(
        before: Mapping[str, Any],
        after: Mapping[str, Any],
    ) -> CheckResult:
        return _combine_any(predicate(before, after) for predicate in predicates)

    return check


def _combine_results(results: Iterable[CheckResult]) -> CheckResult:
    unknowns: list[str] = []
    for result in results:
        if result.verdict is Verdict.FAIL:
            return result
        if result.verdict is Verdict.UNKNOWN:
            unknowns.append(result.reason)
    if unknowns:
        return CheckResult(Verdict.UNKNOWN, "; ".join(unknowns))
    return CheckResult(Verdict.PASS, "all predicates passed")


def _combine_any(results: Iterable[CheckResult]) -> CheckResult:
    failures: list[str] = []
    saw_unknown = False
    for result in results:
        if result.verdict is Verdict.PASS:
            return result
        if result.verdict is Verdict.UNKNOWN:
            saw_unknown = True
        failures.append(result.reason)
    return CheckResult(
        Verdict.UNKNOWN if saw_unknown else Verdict.FAIL,
        "; ".join(failures),
    )


def verify_required(results: Iterable[CheckResult]) -> CheckResult:
    """Preserve UNKNOWN while enforcing the rule that only PASS may advance."""
    unknowns: list[str] = []
    for result in results:
        if result.verdict is Verdict.FAIL:
            return result
        if result.verdict is Verdict.UNKNOWN:
            unknowns.append(result.reason)
    if unknowns:
        return CheckResult(Verdict.UNKNOWN, "; ".join(unknowns))
    return CheckResult(Verdict.PASS, "all required checks passed")
