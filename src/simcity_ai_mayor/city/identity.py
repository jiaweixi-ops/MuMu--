from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace

from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    CityMap,
    GridPoint,
    PlacedBuilding,
)


class IdentityReconciliationError(RuntimeError):
    """Raised when a fresh map cannot be reconciled without guessing identity."""


@dataclass(frozen=True, slots=True)
class BuildingFingerprint:
    catalog_key: str
    kind: BuildingKind
    width: int
    height: int
    population: int
    service_radius: int
    beauty_radius: int
    pollution: float
    traffic_load: float
    movable: bool


@dataclass(frozen=True, slots=True)
class IdentityMatch:
    stable_id: str
    scanned_id: str
    previous_origin: GridPoint
    current_origin: GridPoint


@dataclass(frozen=True, slots=True)
class IdentityReconciliation:
    city: CityMap
    matches: tuple[IdentityMatch, ...]


class BuildingIdentityReconciler:
    """Restore stable building IDs after a fresh template-based map scan.

    Snapshot detectors intentionally include the detected origin in `building_id`, so an
    object that moves receives a different snapshot ID. This reconciler does not use a
    nearest-neighbour guess. Each prior building must be found at exactly its previous
    position, except for moves explicitly supplied by the caller.
    """

    def reconcile(
        self,
        previous: CityMap,
        scanned: CityMap,
        *,
        expected_moves: Mapping[str, GridPoint] | None = None,
    ) -> IdentityReconciliation:
        if previous.width != scanned.width or previous.height != scanned.height:
            raise IdentityReconciliationError("map dimensions changed across rescan")
        if len(previous.buildings) != len(scanned.buildings):
            raise IdentityReconciliationError(
                "building count changed across rescan; refusing identity guess"
            )

        moves = dict(expected_moves or {})
        previous_ids = {building.spec.building_id for building in previous.buildings}
        unknown = set(moves) - previous_ids
        if unknown:
            raise IdentityReconciliationError(
                f"expected move references unknown building IDs: {sorted(unknown)!r}"
            )

        available = list(scanned.buildings)
        restored: list[PlacedBuilding] = []
        matches: list[IdentityMatch] = []

        for prior in previous.buildings:
            stable_id = prior.spec.building_id
            expected_origin = moves.get(stable_id, prior.origin)
            fingerprint = self.fingerprint(prior)
            candidates = [
                building
                for building in available
                if building.origin == expected_origin
                and self.fingerprint(building) == fingerprint
            ]
            if len(candidates) != 1:
                raise IdentityReconciliationError(
                    f"building {stable_id!r} expected at {expected_origin} matched "
                    f"{len(candidates)} candidates"
                )

            matched = candidates[0]
            available.remove(matched)
            restored.append(
                PlacedBuilding(
                    replace(matched.spec, building_id=stable_id),
                    matched.origin,
                )
            )
            matches.append(
                IdentityMatch(
                    stable_id=stable_id,
                    scanned_id=matched.spec.building_id,
                    previous_origin=prior.origin,
                    current_origin=matched.origin,
                )
            )

        if available:
            extras = sorted(building.spec.building_id for building in available)
            raise IdentityReconciliationError(
                f"unmatched buildings remain after reconciliation: {extras!r}"
            )

        return IdentityReconciliation(
            city=scanned.with_buildings(restored),
            matches=tuple(matches),
        )

    @staticmethod
    def fingerprint(building: PlacedBuilding) -> BuildingFingerprint:
        spec = building.spec
        return BuildingFingerprint(
            catalog_key=BuildingIdentityReconciler.catalog_key(spec.building_id),
            kind=spec.kind,
            width=spec.width,
            height=spec.height,
            population=spec.population,
            service_radius=spec.service_radius,
            beauty_radius=spec.beauty_radius,
            pollution=spec.pollution,
            traffic_load=spec.traffic_load,
            movable=spec.movable,
        )

    @staticmethod
    def catalog_key(building_id: str) -> str:
        """Return the detector/template prefix while ignoring snapshot coordinates."""
        return building_id.split("@", 1)[0]
