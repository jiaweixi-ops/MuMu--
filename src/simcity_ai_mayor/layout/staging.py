from __future__ import annotations

from dataclasses import dataclass, replace

from simcity_ai_mayor.city.map_model import CityMap, GridPoint, PlacedBuilding
from simcity_ai_mayor.layout.construction import (
    ConstructionPlan,
    ConstructionStep,
    ConstructionStepKind,
)
from simcity_ai_mayor.layout.joint_optimizer import JointLayoutPlan


@dataclass(frozen=True, slots=True)
class StagingResolution:
    plan: ConstructionPlan
    staging_points: tuple[tuple[str, GridPoint], ...]

    @property
    def fully_resolved(self) -> bool:
        return not self.plan.unresolved_steps


class StagingSolver:
    """Resolve pure MOVE_BUILDING dependency cycles using temporary free cells.

    The solver never stages onto another building's current or final footprint and only
    uses cells with road access in the *current* road network. Cycles involving road
    construction remain unresolved because resolving them safely requires live UI/map
    knowledge about when a new road becomes usable.
    """

    def resolve(
        self,
        original: CityMap,
        target: JointLayoutPlan,
        construction: ConstructionPlan,
    ) -> StagingResolution:
        if not construction.unresolved_steps:
            return StagingResolution(construction, ())

        unresolved_moves = {
            step.building_id: step
            for step in construction.unresolved_steps
            if step.kind is ConstructionStepKind.MOVE_BUILDING
            and step.building_id is not None
        }
        unresolved_other = tuple(
            step
            for step in construction.unresolved_steps
            if step.kind is not ConstructionStepKind.MOVE_BUILDING
        )
        if not unresolved_moves:
            return StagingResolution(construction, ())

        original_by_id = {
            building.spec.building_id: building for building in original.buildings
        }
        target_by_id = {
            building.spec.building_id: building for building in target.resulting_map.buildings
        }
        if set(unresolved_moves) - set(original_by_id):
            raise ValueError("unresolved move references unknown original building")
        if set(unresolved_moves) - set(target_by_id):
            raise ValueError("unresolved move references unknown target building")

        current_positions = {
            building_id: building.origin
            for building_id, building in original_by_id.items()
        }
        pending = dict(unresolved_moves)
        resolved_steps: list[ConstructionStep] = list(construction.ordered_steps)
        completed_ids = {step.step_id for step in resolved_steps}
        staging_points: list[tuple[str, GridPoint]] = []
        stage_counter = 0

        while pending:
            ready_id = self._first_ready_move(
                pending,
                current_positions,
                original_by_id,
            )
            if ready_id is not None:
                step = pending.pop(ready_id)
                destination = self._required_destination(step)
                source = current_positions[ready_id]
                resolved_steps.append(replace(step, source=source, depends_on=()))
                current_positions[ready_id] = destination
                completed_ids.add(step.step_id)
                continue

            pivot_id = sorted(pending)[0]
            pivot = original_by_id[pivot_id]
            staging = self._find_staging_cell(
                original,
                pivot,
                current_positions,
                original_by_id,
                target_by_id,
            )
            if staging is None:
                break
            stage_counter += 1
            stage_id = f"staging:{pivot_id}:{stage_counter}"
            resolved_steps.append(
                ConstructionStep(
                    step_id=stage_id,
                    kind=ConstructionStepKind.MOVE_BUILDING,
                    building_id=pivot_id,
                    source=current_positions[pivot_id],
                    destination=staging,
                    depends_on=(),
                )
            )
            current_positions[pivot_id] = staging
            staging_points.append((pivot_id, staging))

        remaining: list[ConstructionStep] = list(pending.values())
        for step in unresolved_other:
            if set(step.depends_on) <= completed_ids:
                resolved_steps.append(step)
                completed_ids.add(step.step_id)
            else:
                remaining.append(step)

        return StagingResolution(
            ConstructionPlan(tuple(resolved_steps), tuple(sorted(remaining, key=lambda s: s.step_id))),
            tuple(staging_points),
        )

    @staticmethod
    def _required_destination(step: ConstructionStep) -> GridPoint:
        if step.destination is None:
            raise ValueError(f"move step {step.step_id!r} has no destination")
        return step.destination

    def _first_ready_move(
        self,
        pending: dict[str, ConstructionStep],
        current_positions: dict[str, GridPoint],
        original_by_id: dict[str, PlacedBuilding],
    ) -> str | None:
        occupied = self._occupied_by(current_positions, original_by_id)
        for building_id in sorted(pending):
            step = pending[building_id]
            destination = self._required_destination(step)
            footprint = self._footprint_at(original_by_id[building_id], destination)
            blockers = {
                occupant
                for point in footprint
                if (occupant := occupied.get(point)) is not None
                and occupant != building_id
            }
            if not blockers:
                return building_id
        return None

    def _find_staging_cell(
        self,
        city: CityMap,
        pivot: PlacedBuilding,
        current_positions: dict[str, GridPoint],
        original_by_id: dict[str, PlacedBuilding],
        target_by_id: dict[str, PlacedBuilding],
    ) -> GridPoint | None:
        occupied = self._occupied_by(current_positions, original_by_id)
        reserved_final = {
            point
            for building_id, building in target_by_id.items()
            if building_id != pivot.spec.building_id
            for point in building.footprint()
        }
        candidates: list[GridPoint] = []
        for y in range(city.height - pivot.spec.height + 1):
            for x in range(city.width - pivot.spec.width + 1):
                origin = GridPoint(x, y)
                footprint = self._footprint_at(pivot, origin)
                if footprint & reserved_final:
                    continue
                if any(
                    point in city.blocked or point in city.roads
                    for point in footprint
                ):
                    continue
                if any(
                    point in occupied
                    and occupied[point] != pivot.spec.building_id
                    for point in footprint
                ):
                    continue
                if not any(
                    neighbor in city.roads
                    for point in footprint
                    for neighbor in point.neighbors4()
                ):
                    continue
                candidates.append(origin)
        if not candidates:
            return None
        current = current_positions[pivot.spec.building_id]
        return min(
            candidates,
            key=lambda point: (
                abs(point.x - current.x) + abs(point.y - current.y),
                point.y,
                point.x,
            ),
        )

    @staticmethod
    def _occupied_by(
        positions: dict[str, GridPoint],
        original_by_id: dict[str, PlacedBuilding],
    ) -> dict[GridPoint, str]:
        occupied: dict[GridPoint, str] = {}
        for building_id, origin in positions.items():
            for point in StagingSolver._footprint_at(original_by_id[building_id], origin):
                occupied[point] = building_id
        return occupied

    @staticmethod
    def _footprint_at(
        building: PlacedBuilding,
        origin: GridPoint,
    ) -> frozenset[GridPoint]:
        return frozenset(
            GridPoint(origin.x + dx, origin.y + dy)
            for dx in range(building.spec.width)
            for dy in range(building.spec.height)
        )
