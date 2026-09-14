from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from enum import StrEnum

from simcity_ai_mayor.city.map_model import CityMap, GridPoint
from simcity_ai_mayor.layout.joint_optimizer import JointLayoutPlan
from simcity_ai_mayor.layout.road_optimizer import RoadEditKind


class ConstructionStepKind(StrEnum):
    BUILD_ROAD = "BUILD_ROAD"
    MOVE_BUILDING = "MOVE_BUILDING"
    REMOVE_ROAD = "REMOVE_ROAD"


@dataclass(frozen=True, slots=True)
class ConstructionStep:
    step_id: str
    kind: ConstructionStepKind
    building_id: str | None = None
    source: GridPoint | None = None
    destination: GridPoint | None = None
    point: GridPoint | None = None
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ConstructionPlan:
    ordered_steps: tuple[ConstructionStep, ...]
    unresolved_steps: tuple[ConstructionStep, ...]

    @property
    def requires_staging(self) -> bool:
        return bool(self.unresolved_steps)


class ConstructionPlanner:
    """Convert a target layout into dependency-aware physical construction steps.

    It never invents a temporary staging position. If building moves form a dependency
    cycle (for example a swap), those steps remain unresolved and the caller must find
    and verify temporary free space before executing them on the game.
    """

    def build(self, original: CityMap, plan: JointLayoutPlan) -> ConstructionPlan:
        if (
            original.width != plan.resulting_map.width
            or original.height != plan.resulting_map.height
        ):
            raise ValueError("original and resulting map dimensions differ")

        steps: dict[str, ConstructionStep] = {}
        dependencies: dict[str, set[str]] = defaultdict(set)

        move_ids: dict[str, str] = {}
        for move in plan.moves:
            step_id = f"move:{move.building_id}"
            move_ids[move.building_id] = step_id
            steps[step_id] = ConstructionStep(
                step_id=step_id,
                kind=ConstructionStepKind.MOVE_BUILDING,
                building_id=move.building_id,
                source=move.source,
                destination=move.destination,
            )

        build_road_ids: dict[GridPoint, str] = {}
        remove_road_ids: dict[GridPoint, str] = {}
        for edit in plan.road_edits:
            if edit.kind is RoadEditKind.BUILD:
                step_id = f"road:add:{edit.point.x}:{edit.point.y}"
                build_road_ids[edit.point] = step_id
                steps[step_id] = ConstructionStep(
                    step_id=step_id,
                    kind=ConstructionStepKind.BUILD_ROAD,
                    point=edit.point,
                )
            else:
                step_id = f"road:remove:{edit.point.x}:{edit.point.y}"
                remove_road_ids[edit.point] = step_id
                steps[step_id] = ConstructionStep(
                    step_id=step_id,
                    kind=ConstructionStepKind.REMOVE_ROAD,
                    point=edit.point,
                )

        original_buildings = {
            building.spec.building_id: building for building in original.buildings
        }
        resulting_buildings = {
            building.spec.building_id: building for building in plan.resulting_map.buildings
        }

        # A destination occupied by another moving building's original footprint must
        # wait until that building has moved away.
        for building_id, move_step_id in move_ids.items():
            destination = resulting_buildings[building_id].footprint()
            for other_id, other_step_id in move_ids.items():
                if other_id == building_id:
                    continue
                if destination & original_buildings[other_id].footprint():
                    dependencies[move_step_id].add(other_step_id)

        # A new road on a building's current footprint can only be built after that
        # building moves away.
        for point, road_step_id in build_road_ids.items():
            for building_id, move_step_id in move_ids.items():
                if point in original_buildings[building_id].footprint():
                    dependencies[road_step_id].add(move_step_id)

        # If a moved building's final road access relies on a newly built road, ensure
        # that road exists before the move.
        for building_id, move_step_id in move_ids.items():
            final_building = resulting_buildings[building_id]
            neighboring = {
                neighbor
                for cell in final_building.footprint()
                for neighbor in cell.neighbors4()
            }
            for point, road_step_id in build_road_ids.items():
                if point in neighboring:
                    dependencies[move_step_id].add(road_step_id)

        # Road removals are destructive and therefore run after every building move and
        # every road addition. This is conservative but makes the generated prefix safe.
        prerequisites = set(move_ids.values()) | set(build_road_ids.values())
        for remove_step_id in remove_road_ids.values():
            dependencies[remove_step_id].update(prerequisites)

        return self._topological_plan(steps, dependencies)

    @staticmethod
    def _topological_plan(
        steps: dict[str, ConstructionStep],
        dependencies: dict[str, set[str]],
    ) -> ConstructionPlan:
        reverse: dict[str, set[str]] = defaultdict(set)
        indegree: dict[str, int] = {}
        for step_id in steps:
            deps = {dep for dep in dependencies.get(step_id, set()) if dep in steps}
            dependencies[step_id] = deps
            indegree[step_id] = len(deps)
            for dep in deps:
                reverse[dep].add(step_id)

        ready = deque(sorted(step_id for step_id, degree in indegree.items() if degree == 0))
        ordered_ids: list[str] = []
        while ready:
            step_id = ready.popleft()
            ordered_ids.append(step_id)
            for dependent in sorted(reverse.get(step_id, set())):
                indegree[dependent] -= 1
                if indegree[dependent] == 0:
                    ready.append(dependent)

        unresolved_ids = sorted(set(steps) - set(ordered_ids))
        ordered = tuple(
            ConstructionPlanner._with_dependencies(steps[step_id], dependencies[step_id])
            for step_id in ordered_ids
        )
        unresolved = tuple(
            ConstructionPlanner._with_dependencies(steps[step_id], dependencies[step_id])
            for step_id in unresolved_ids
        )
        return ConstructionPlan(ordered, unresolved)

    @staticmethod
    def _with_dependencies(
        step: ConstructionStep,
        dependencies: set[str],
    ) -> ConstructionStep:
        return ConstructionStep(
            step_id=step.step_id,
            kind=step.kind,
            building_id=step.building_id,
            source=step.source,
            destination=step.destination,
            point=step.point,
            depends_on=tuple(sorted(dependencies)),
        )
