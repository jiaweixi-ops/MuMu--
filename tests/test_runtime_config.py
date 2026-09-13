import json

import pytest

from simcity_ai_mayor.core.models import RunMode, ScreenType
from simcity_ai_mayor.runtime.config import AppConfig, RuntimeConfigError


def write_config(tmp_path, **overrides):
    payload = {
        "device_id": "127.0.0.1:7555",
        "package_name": "com.example.simcity",
        "expected_size": [1280, 720],
        "run_mode": "DRY_RUN",
        "vision": {
            "storage": {
                "roi": [10, 20, 100, 30],
                "screens": ["FACTORY"],
            }
        },
        "planner": {
            "collect_tap": [100, 200],
            "production": {"item": "metal", "tap": [300, 400]},
        },
    }
    payload.update(overrides)
    path = tmp_path / "v0.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_config_loads_safe_defaults_and_relative_paths(tmp_path) -> None:
    config = AppConfig.load(write_config(tmp_path))

    assert config.device_id == "127.0.0.1:7555"
    assert config.run_mode is RunMode.DRY_RUN
    assert config.duration_seconds == 14400
    assert config.expected_size == (1280, 720)
    assert config.state_db == (tmp_path / "data/runtime/state.db").resolve()
    assert config.vision.storage_roi is not None
    assert config.vision.storage_screens == frozenset({ScreenType.FACTORY})
    assert config.collect_tap is not None
    assert config.production is not None
    assert config.production.item == "metal"


def test_config_requires_frozen_expected_size(tmp_path) -> None:
    path = write_config(tmp_path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload.pop("expected_size")
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(RuntimeConfigError, match="expected_size"):
        AppConfig.load(path)


def test_config_rejects_unknown_run_mode(tmp_path) -> None:
    path = write_config(tmp_path, run_mode="MAGIC")

    with pytest.raises(ValueError):
        AppConfig.load(path)
