from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from typing import Protocol

from simcity_ai_mayor.city.identity import (
    BuildingIdentityReconciler,
    IdentityReconciliationError,
)
from simcity_ai_mayor.city.map_model import CityMap
from simcity_ai_mayor.layout.construction import (
    ConstructionPlan,
    ConstructionStep,
    ConstructionStepKind,
)


class ConstructionExecutionStatus(StrEnum):
    COMPLETE = "COMPLETE"
    BLOCKED_UNRESOLVED = "BLOCKED_UNRESOLVED"
    PRECONDITION_FAILED = "PRECONDITION_FAILED"
    ACTION_FAILED = "ACTION_FAILED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"
    STOPPED = "STOPPED"


class ConstructionActionAdapter(Protocol):
    def execute(self, step: ConstructionStep, current: CityMap) -> None: ...


class LiveCityMapScanner(Protocol):
    def scan(self) -> CityMap: ...


@dataclass(frozen=True, slots=True)
class ConstructionStepRecord:
    step: ConstructionStep
    before: CityMap
    after: CityMap


@dataclass(frozen=True, slots=True)
class ConstructionExecutionReport:
    status: ConstructionExecutionStatus
    current_map: CityMap
    completed_steps: tuple[ConstructionStepRecord, ...] = ()
    failed_step: ConstructionStep | None = None
    error: str | None = None


class ConstructionExecutor:
    """Execute a construction plan one verified physical step at a time.

    After every action the executor requires a fresh CityMap scan, restores stable
    building identities, computes the exact expected map transition and refuses to
    continue if *anything* else changed. The game-specific adapter owns gestures; this
    state machine owns ordering, preconditions and fail-closed verification.
    """

    def __init__(
        self,
        *,
        adapter: ConstructionActionAdapter,
        scanner: LiveCityMapScanner,
        reconciler: BuildingIdentityReconciler | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> None:
        self.adapter = adapter
        self.scanner = scanner
        self.reconciler = reconciler or BuildingIdentityReconciler()
        self.stop_requested = stop_requested or (lambda: False)

    def run(
        self,
        baseline: CityMap,
        plan: ConstructionPlan,
    ) -> ConstructionExecutionReport:
        if plan.unresolved_steps:
            return ConstructionExecutionReport(
                ConstructionExecutionStatus.BLOCKED_UNRESOLVED,
                baseline,
                error="construction plan still contains unresolved dependency steps",
            )

        try:
            current = self._scan_and_reconcile(baseline, expected_moves={})
        except Exception as exc:
            return ConstructionExecutionReport(
                ConstructionExecutionStatus.VERIFICATION_FAILED,
                baseline,
                error=f"initial map verification failed: {type(exc).__name__}: {exc}",
            )
        if current != baseline:
            return ConstructionExecutionReport(
                ConstructionExecutionStatus.PRECONDITION_FAILED,
                current,
                error="live map no longer equals the construction baseline",
            )

        completed: list[ConstructionStepRecord] = []
        completed_ids: set[str] = set()

        for step in plan.ordered_steps:
            if self.stop_requested():
                return ConstructionExecutionReport(
                    ConstructionExecutionStatus.STOPPED,
                    current,
                    tuple(completed),
                    failed_step=step,
                    error="stop requested before construction step",
                )

            missing_dependencies = set(step.depends_on) - completed_ids
            if missing_dependencies:
                return ConstructionExecutionReport(
                    ConstructionExecutionStatus.PRECONDITION_FAILED,
                    current,
                    tuple(completed),
                    failed_step=step,
                    error=(
                        f"step dependencies are incomplete: "
                        f"{sorted(missing_dependencies)!r}"
                    ),
                )

            try:
                expected = self._apply_expected_step(current, step)
            except Exception as exc:
                return ConstructionExecutionReport(
                    ConstructionExecutionStatus.PRECONDITION_FAILED,
                    current,
                    tuple(completed),
                    failed_step=step,
                    error=f"invalid step precondition: {type(exc).__name__}: {exc}",
                )

            try:
                self.adapter.execute(step, current)
            except Exception as exc:
                return ConstructionExecutionReport(
                    ConstructionExecutionStatus.ACTION_FAILED,
                    current,
                    tuple(completed),
                    failed_step=step,
                    error=f"action failed: {type(exc).__name__}: {exc}",
                )

            expected_moves = self._expected_moves(step)
            try:
                observed = self._scan_and_reconcile(
                    current,
                    expected_moves=expected_moves,
                )
            except Exception as exc:
                return ConstructionExecutionReport(
                    ConstructionExecutionStatus.VERIFICATION_FAILED,
                    current,
                    tuple(completed),
                    failed_step=step,
                    error=f"fresh map verification failed: {type(exc).__name__}: {exc}",
                )

            if observed != expected:
                return ConstructionExecutionReport(
                    ConstructionExecutionStatus.VERIFICATION_FAILED,
                    observed,
                    tuple(completed),
                    failed_step=step,
                    error="fresh map contains changes outside the declared construction step",
                )

            completed.append(ConstructionStepRecord(step, current, observed))
            completed_ids.add(step.step_id)
            current = observed

            if self.stop_requested():
                return ConstructionExecutionReport(
                    ConstructionExecutionStatus.STOPPED,
                    current,
                    tuple(completed),
                    error="stop requested after verified construction step",
                )

        return ConstructionExecutionReport(
            ConstructionExecutionStatus.COMPLETE,
            current,
            tuple(completed),
        )

    def _scan_and_reconcile(
        self,
        previous: CityMap,
        *,
        expected_moves: dict[str, object],
    ) -> CityMap:
        scanned = self.scanner.scan()
        try:
            return self.reconciler.reconcile(
                previous,
                scanned,
                expected_moves=expected_moves,
            ).city
        except IdentityReconciliationError:
            raise

    @staticmethod
    def _expected_moves(step: ConstructionStep) -> dict[str, object]:
        if step.kind is not ConstructionStepKind.MOVE_BUILDING:
            return {}
        if step.building_id is None or step.destination is None:
            raise ValueError("move step requires building_id and destination")
        return {step.building_id: step.destination}

    @staticmethod
    def _apply_expected_step(current: CityMap, step: ConstructionStep) -> CityMap:
        if step.kind is ConstructionStepKind.MOVE_BUILDING:
            if step.building_id is None or step.source is None or step.destination is None:
                raise ValueError("move step requires building_id, source and destination")
            building = current.building(step.building_id)
            if building.origin != step.source:
                raise ValueError(
                    f"building {step.building_id!r} is at {building.origin}, "
                    f"not declared source {step.source}"
                )
            return current.move_building(step.building_id, step.destination)

        if step.point is None:
            raise ValueError(f"{step.kind.value} step requires point")
        point = step.point
        if not current.in_bounds(point):
            raise ValueError(f"road point out of bounds: {point}")

        if step.kind is ConstructionStepKind.BUILD_ROAD:
            if point in current.roads:
                raise ValueError(f"road already exists at {point}")
            if point in current.blocked:
                raise ValueError(f"road point is blocked: {point}")
            if any(point in building.footprint() for building in current.buildings):
                raise ValueError(f"road point is occupied by a building: {point}")
            return replace(current, roads=current.roads | {point})

        if step.kind is ConstructionStepKind.REMOVE_ROAD:
            if point not in current.roads:
                raise ValueError(f"road does not exist at {point}")
            return replace(current, roads=current.roads - {point})

        raise ValueError(f"unsupported construction step kind: {step.kind!r}")
