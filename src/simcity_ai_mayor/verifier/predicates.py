from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Iterable, Mapping, Any


class Verdict(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class CheckResult:
    verdict: Verdict
    reason: str


Predicate = Callable[[Mapping[str, Any]], CheckResult]


def screen_eq(expected: str) -> Predicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        actual = state.get("screen")
        if actual is None:
            return CheckResult(Verdict.UNKNOWN, "screen missing from state")
        if actual == expected:
            return CheckResult(Verdict.PASS, f"screen == {expected}")
        return CheckResult(Verdict.FAIL, f"screen={actual!r}, expected={expected!r}")

    return check


def state_eq(path: str, expected: Any) -> Predicate:
    keys = path.split(".")

    def check(state: Mapping[str, Any]) -> CheckResult:
        current: Any = state
        for key in keys:
            if not isinstance(current, Mapping) or key not in current:
                return CheckResult(Verdict.UNKNOWN, f"state path missing: {path}")
            current = current[key]
        if current == expected:
            return CheckResult(Verdict.PASS, f"{path} == {expected!r}")
        return CheckResult(Verdict.FAIL, f"{path}={current!r}, expected={expected!r}")

    return check


def counter_delta(counter: str, minimum: int, maximum: int) -> Callable[[Mapping[str, Any], Mapping[str, Any]], CheckResult]:
    def check(before: Mapping[str, Any], after: Mapping[str, Any]) -> CheckResult:
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


def all_of(*predicates: Predicate) -> Predicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        unknowns: list[str] = []
        for predicate in predicates:
            result = predicate(state)
            if result.verdict is Verdict.FAIL:
                return result
            if result.verdict is Verdict.UNKNOWN:
                unknowns.append(result.reason)
        if unknowns:
            return CheckResult(Verdict.UNKNOWN, "; ".join(unknowns))
        return CheckResult(Verdict.PASS, "all predicates passed")

    return check


def any_of(*predicates: Predicate) -> Predicate:
    def check(state: Mapping[str, Any]) -> CheckResult:
        failures: list[str] = []
        saw_unknown = False
        for predicate in predicates:
            result = predicate(state)
            if result.verdict is Verdict.PASS:
                return result
            if result.verdict is Verdict.UNKNOWN:
                saw_unknown = True
            failures.append(result.reason)
        verdict = Verdict.UNKNOWN if saw_unknown else Verdict.FAIL
        return CheckResult(verdict, "; ".join(failures))

    return check


def verify_required(results: Iterable[CheckResult]) -> CheckResult:
    """Collapse checks using the V0 rule: UNKNOWN is never treated as success."""

    reasons: list[str] = []
    for result in results:
        if result.verdict is not Verdict.PASS:
            reasons.append(result.reason)
    if reasons:
        return CheckResult(Verdict.FAIL, "; ".join(reasons))
    return CheckResult(Verdict.PASS, "all required checks passed")
