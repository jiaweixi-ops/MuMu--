import pytest

from simcity_ai_mayor.core.models import (
    AutomationState,
    ScreenType,
    StorageCapacity,
)
from simcity_ai_mayor.runtime.orchestrator import Observation


def test_observation_requires_positive_frame_identity() -> None:
    with pytest.raises(ValueError, match="frame_id"):
        Observation(
            device_id="mumu-0",
            frame_id=0,
            captured_at=1.0,
            screen=ScreenType.CITY,
            automation_state=AutomationState.OBSERVE,
        )

    with pytest.raises(ValueError, match="captured_at"):
        Observation(
            device_id="mumu-0",
            frame_id=1,
            captured_at=0.0,
            screen=ScreenType.CITY,
            automation_state=AutomationState.OBSERVE,
        )


def test_typed_storage_is_the_verifier_source_of_truth() -> None:
    observation = Observation(
        device_id="mumu-0",
        frame_id=1,
        captured_at=1.0,
        screen=ScreenType.STORAGE,
        automation_state=AutomationState.STORAGE_NORMAL,
        storage=StorageCapacity(used=11, capacity=120, confidence=0.995),
        state={
            "storage_used": 999,
            "storage_capacity": 999,
            "storage_confidence": 0.0,
            "custom": "kept",
        },
    )

    projected = observation.verifier_state()

    assert projected["storage_used"] == 11
    assert projected["storage_capacity"] == 120
    assert projected["storage_confidence"] == 0.995
    assert projected["custom"] == "kept"
    assert projected["screen"] == ScreenType.STORAGE.value
