from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from simcity_ai_mayor.core.models import FactoryState, RunMode, ScreenType, V0Policy
from simcity_ai_mayor.planner.v0 import ProductionRecipe, TapPoint
from simcity_ai_mayor.vision.readers import (
    FactoryTemplateRule,
    Roi,
    ScreenTemplateRule,
    TemplateAnchorSpec,
)


class RuntimeConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class VisionRuntimeConfig:
    screen_rules: tuple[ScreenTemplateRule, ...] = ()
    factory_rules: tuple[FactoryTemplateRule, ...] = ()
    storage_roi: Roi | None = None
    storage_screens: frozenset[ScreenType] = frozenset(
        {ScreenType.FACTORY, ScreenType.STORAGE}
    )
    screen_ambiguity_margin: float = 0.02
    factory_ambiguity_margin: float = 0.02


@dataclass(frozen=True, slots=True)
class AppConfig:
    device_id: str
    package_name: str
    adb_path: str
    state_db: Path
    data_root: Path
    run_mode: RunMode
    duration_seconds: float
    idle_sleep_seconds: float
    action_timeout_seconds: float
    expected_size: tuple[int, int]
    expected_game_version: str | None
    policy: V0Policy
    vision: VisionRuntimeConfig
    collect_tap: TapPoint | None
    production: ProductionRecipe | None

    @classmethod
    def load(cls, path: str | Path) -> AppConfig:
        config_path = Path(path).expanduser().resolve()
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise RuntimeConfigError(f"cannot read config: {config_path}") from exc
        except json.JSONDecodeError as exc:
            raise RuntimeConfigError(f"invalid JSON config: {exc}") from exc
        if not isinstance(raw, dict):
            raise RuntimeConfigError("config root must be a JSON object")
        base = config_path.parent

        device_id = _required_string(raw, "device_id")
        package_name = _required_string(raw, "package_name")
        adb_path = str(raw.get("adb_path", "adb"))
        state_db = _resolve_path(base, raw.get("state_db", "data/runtime/state.db"))
        data_root = _resolve_path(base, raw.get("data_root", "data"))
        run_mode = RunMode(str(raw.get("run_mode", RunMode.DRY_RUN.value)))
        duration_seconds = _positive_float(raw.get("duration_seconds", 14400), "duration_seconds")
        idle_sleep_seconds = _non_negative_float(
            raw.get("idle_sleep_seconds", 0.5), "idle_sleep_seconds"
        )
        action_timeout_seconds = _positive_float(
            raw.get("action_timeout_seconds", 15.0), "action_timeout_seconds"
        )
        expected_size = _size(raw.get("expected_size"))
        expected_game_version_raw = raw.get("expected_game_version")
        expected_game_version = (
            str(expected_game_version_raw).strip() if expected_game_version_raw else None
        )

        policy_raw = raw.get("policy", {})
        if not isinstance(policy_raw, dict):
            raise RuntimeConfigError("policy must be an object")
        try:
            policy = V0Policy(**policy_raw)
        except (TypeError, ValueError) as exc:
            raise RuntimeConfigError(f"invalid policy: {exc}") from exc

        vision = _parse_vision(base, raw.get("vision", {}))
        planner_raw = raw.get("planner", {})
        if not isinstance(planner_raw, dict):
            raise RuntimeConfigError("planner must be an object")
        collect_tap = _optional_tap(planner_raw.get("collect_tap"))
        production = _optional_production(planner_raw.get("production"))

        return cls(
            device_id=device_id,
            package_name=package_name,
            adb_path=adb_path,
            state_db=state_db,
            data_root=data_root,
            run_mode=run_mode,
            duration_seconds=duration_seconds,
            idle_sleep_seconds=idle_sleep_seconds,
            action_timeout_seconds=action_timeout_seconds,
            expected_size=expected_size,
            expected_game_version=expected_game_version,
            policy=policy,
            vision=vision,
            collect_tap=collect_tap,
            production=production,
        )


def _parse_vision(base: Path, raw: Any) -> VisionRuntimeConfig:
    if not isinstance(raw, dict):
        raise RuntimeConfigError("vision must be an object")
    screen_rules = tuple(
        _parse_screen_rule(base, item) for item in _list(raw.get("screens", []), "vision.screens")
    )
    factory_rules = tuple(
        _parse_factory_rule(base, item)
        for item in _list(raw.get("factory_states", []), "vision.factory_states")
    )

    storage_raw = raw.get("storage")
    storage_roi: Roi | None = None
    storage_screens = frozenset({ScreenType.FACTORY, ScreenType.STORAGE})
    if storage_raw is not None:
        if not isinstance(storage_raw, dict):
            raise RuntimeConfigError("vision.storage must be an object")
        storage_roi = Roi.from_sequence(_list(storage_raw.get("roi"), "vision.storage.roi"))
        screens_raw = storage_raw.get("screens", ["FACTORY", "STORAGE"])
        storage_screens = frozenset(
            ScreenType(str(value)) for value in _list(screens_raw, "vision.storage.screens")
        )

    return VisionRuntimeConfig(
        screen_rules=screen_rules,
        factory_rules=factory_rules,
        storage_roi=storage_roi,
        storage_screens=storage_screens,
        screen_ambiguity_margin=_bounded_float(
            raw.get("screen_ambiguity_margin", 0.02),
            "vision.screen_ambiguity_margin",
            0.0,
            1.0,
        ),
        factory_ambiguity_margin=_bounded_float(
            raw.get("factory_ambiguity_margin", 0.02),
            "vision.factory_ambiguity_margin",
            0.0,
            1.0,
        ),
    )


def _parse_screen_rule(base: Path, raw: Any) -> ScreenTemplateRule:
    if not isinstance(raw, dict):
        raise RuntimeConfigError("screen rule must be an object")
    screen = ScreenType(_required_string(raw, "screen"))
    anchors = tuple(
        _parse_anchor(base, item)
        for item in _list(raw.get("anchors"), f"screen {screen.value} anchors")
    )
    return ScreenTemplateRule(screen, anchors)


def _parse_factory_rule(base: Path, raw: Any) -> FactoryTemplateRule:
    if not isinstance(raw, dict):
        raise RuntimeConfigError("factory-state rule must be an object")
    state = FactoryState(_required_string(raw, "state"))
    anchors = tuple(
        _parse_anchor(base, item)
        for item in _list(raw.get("anchors"), f"factory state {state.value} anchors")
    )
    return FactoryTemplateRule(state, anchors)


def _parse_anchor(base: Path, raw: Any) -> TemplateAnchorSpec:
    if not isinstance(raw, dict):
        raise RuntimeConfigError("template anchor must be an object")
    roi = Roi.from_sequence(_list(raw.get("roi"), "template anchor roi"))
    template = _resolve_path(base, _required_string(raw, "template"))
    threshold = _bounded_float(raw.get("threshold", 0.92), "template threshold", 0.0, 1.0)
    weight = _positive_float(raw.get("weight", 1.0), "template weight")
    return TemplateAnchorSpec(roi=roi, template_path=template, threshold=threshold, weight=weight)


def _optional_tap(raw: Any) -> TapPoint | None:
    if raw is None:
        return None
    values = _list(raw, "tap point")
    if len(values) != 2:
        raise RuntimeConfigError("tap point must contain [x, y]")
    return TapPoint(int(values[0]), int(values[1]))


def _optional_production(raw: Any) -> ProductionRecipe | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise RuntimeConfigError("planner.production must be an object")
    item = _required_string(raw, "item")
    tap = _optional_tap(raw.get("tap"))
    if tap is None:
        raise RuntimeConfigError("planner.production.tap is required")
    return ProductionRecipe(item, tap)


def _required_string(raw: dict[str, Any], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeConfigError(f"{key} is required")
    return value.strip()


def _resolve_path(base: Path, value: Any) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise RuntimeConfigError("path value is required")
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _list(raw: Any, name: str) -> list[Any]:
    if not isinstance(raw, list):
        raise RuntimeConfigError(f"{name} must be an array")
    return raw


def _size(raw: Any) -> tuple[int, int]:
    values = _list(raw, "expected_size")
    if len(values) != 2:
        raise RuntimeConfigError("expected_size must contain [width, height]")
    width, height = int(values[0]), int(values[1])
    if width <= 0 or height <= 0:
        raise RuntimeConfigError("expected_size values must be > 0")
    return width, height


def _positive_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError(f"{name} must be numeric") from exc
    if parsed <= 0:
        raise RuntimeConfigError(f"{name} must be > 0")
    return parsed


def _non_negative_float(value: Any, name: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeConfigError(f"{name} must be numeric") from exc
    if parsed < 0:
        raise RuntimeConfigError(f"{name} must be >= 0")
    return parsed


def _bounded_float(value: Any, name: str, minimum: float, maximum: float) -> float:
    parsed = _non_negative_float(value, name)
    if not minimum <= parsed <= maximum:
        raise RuntimeConfigError(f"{name} must be between {minimum} and {maximum}")
    return parsed
