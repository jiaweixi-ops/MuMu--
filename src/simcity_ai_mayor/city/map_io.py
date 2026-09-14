from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from simcity_ai_mayor.city.map_model import (
    BuildingKind,
    BuildingSpec,
    CityMap,
    GridPoint,
    PlacedBuilding,
)


class CityMapFormatError(ValueError):
    pass


def load_city_map(path: str | Path) -> CityMap:
    source = Path(path).expanduser().resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CityMapFormatError(f"cannot read city map: {source}") from exc
    except json.JSONDecodeError as exc:
        raise CityMapFormatError(f"invalid city map JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise CityMapFormatError("city map root must be an object")

    width = _positive_int(raw.get("width"), "width")
    height = _positive_int(raw.get("height"), "height")
    roads = frozenset(_point(item, "roads") for item in _list(raw.get("roads", []), "roads"))
    blocked = frozenset(
        _point(item, "blocked") for item in _list(raw.get("blocked", []), "blocked")
    )
    buildings = tuple(
        _building(item) for item in _list(raw.get("buildings", []), "buildings")
    )
    try:
        return CityMap(width, height, roads=roads, blocked=blocked, buildings=buildings)
    except ValueError as exc:
        raise CityMapFormatError(str(exc)) from exc


def city_map_to_dict(city: CityMap) -> dict[str, Any]:
    return {
        "width": city.width,
        "height": city.height,
        "roads": [[point.x, point.y] for point in sorted(city.roads)],
        "blocked": [[point.x, point.y] for point in sorted(city.blocked)],
        "buildings": [
            {
                "id": building.spec.building_id,
                "kind": building.spec.kind.value,
                "origin": [building.origin.x, building.origin.y],
                "size": [building.spec.width, building.spec.height],
                "population": building.spec.population,
                "service_radius": building.spec.service_radius,
                "beauty_radius": building.spec.beauty_radius,
                "pollution": building.spec.pollution,
                "traffic_load": building.spec.traffic_load,
                "movable": building.spec.movable,
            }
            for building in city.buildings
        ],
    }


def _building(raw: Any) -> PlacedBuilding:
    if not isinstance(raw, dict):
        raise CityMapFormatError("building entry must be an object")
    building_id = _required_string(raw, "id")
    try:
        kind = BuildingKind(_required_string(raw, "kind"))
    except ValueError as exc:
        raise CityMapFormatError(f"invalid building kind for {building_id!r}") from exc
    origin = _point(raw.get("origin"), f"building {building_id} origin")
    size = _list(raw.get("size"), f"building {building_id} size")
    if len(size) != 2:
        raise CityMapFormatError(f"building {building_id} size must contain [width, height]")
    try:
        spec = BuildingSpec(
            building_id=building_id,
            kind=kind,
            width=int(size[0]),
            height=int(size[1]),
            population=int(raw.get("population", 0)),
            service_radius=int(raw.get("service_radius", 0)),
            beauty_radius=int(raw.get("beauty_radius", 0)),
            pollution=float(raw.get("pollution", 0.0)),
            traffic_load=float(raw.get("traffic_load", 0.0)),
            movable=bool(raw.get("movable", True)),
        )
    except (TypeError, ValueError) as exc:
        raise CityMapFormatError(f"invalid building {building_id!r}: {exc}") from exc
    return PlacedBuilding(spec, origin)


def _point(raw: Any, name: str) -> GridPoint:
    values = _list(raw, name)
    if len(values) != 2:
        raise CityMapFormatError(f"{name} point must contain [x, y]")
    try:
        return GridPoint(int(values[0]), int(values[1]))
    except (TypeError, ValueError) as exc:
        raise CityMapFormatError(f"{name} point must be integer coordinates") from exc


def _list(raw: Any, name: str) -> list[Any]:
    if not isinstance(raw, list):
        raise CityMapFormatError(f"{name} must be an array")
    return raw


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise CityMapFormatError(f"{key} is required")
    return value.strip()


def _positive_int(raw: Any, name: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise CityMapFormatError(f"{name} must be an integer") from exc
    if value <= 0:
        raise CityMapFormatError(f"{name} must be > 0")
    return value
