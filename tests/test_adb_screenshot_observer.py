from io import BytesIO

import pytest
from PIL import Image

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    ScreenType,
    StorageCapacity,
)
from simcity_ai_mayor.vision.observer import AdbScreenshotObserver, ObservationBuildError


class FakeRunner:
    def __init__(self, *screenshots: bytes) -> None:
        self.device_id = "mumu-0"
        self._screenshots = list(screenshots)

    def screenshot_png(self) -> bytes:
        return self._screenshots.pop(0)


class SequenceClock:
    def __init__(self, *values: float) -> None:
        self._values = list(values)

    def __call__(self) -> float:
        return self._values.pop(0)


class FactoryScreenClassifier:
    def classify(self, image: Image.Image) -> ScreenType:
        assert image.mode == "RGB"
        return ScreenType.FACTORY


class FullStorageReader:
    def read(self, image: Image.Image, screen: ScreenType) -> StorageCapacity:
        assert image.size == (8, 6)
        assert screen is ScreenType.FACTORY
        return StorageCapacity(used=120, capacity=120, confidence=1.0)


class BlockedFactoryReader:
    def read(self, image: Image.Image, screen: ScreenType) -> FactoryState:
        assert image.mode == "RGB"
        assert screen is ScreenType.FACTORY
        return FactoryState.COMPLETED_STORAGE_BLOCKED


def make_png(width: int = 8, height: int = 6) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (width, height), "white").save(buffer, format="PNG")
    return buffer.getvalue()


def test_default_observer_is_fail_closed_and_monotonic() -> None:
    png = make_png()
    observer = AdbScreenshotObserver(
        runner=FakeRunner(png, png),
        clock=SequenceClock(1.0, 2.0),
        expected_size=(8, 6),
    )

    first = observer.observe()
    second = observer.observe()

    assert first.device_id == "mumu-0"
    assert first.frame_id == 1
    assert second.frame_id == 2
    assert first.captured_at == 1.0
    assert second.captured_at == 2.0
    assert first.screen is ScreenType.UNKNOWN
    assert first.automation_state is AutomationState.RECOVER
    assert first.factory_state is FactoryState.UNKNOWN
    assert first.storage is None
    assert first.verifier_state()["frame_width"] == 8
    assert first.verifier_state()["frame_height"] == 6
    assert first.verifier_state()["frame_mode"] == "RGB"


def test_observer_builds_typed_game_state_from_injected_readers() -> None:
    observer = AdbScreenshotObserver(
        runner=FakeRunner(make_png()),
        screen_classifier=FactoryScreenClassifier(),
        storage_reader=FullStorageReader(),
        factory_reader=BlockedFactoryReader(),
        clock=SequenceClock(5.0),
        expected_size=(8, 6),
    )

    observation = observer.observe()

    assert observation.screen is ScreenType.FACTORY
    assert observation.storage == StorageCapacity(120, 120, 1.0)
    assert observation.factory_state is FactoryState.COMPLETED_STORAGE_BLOCKED
    assert observation.automation_state is AutomationState.BLOCKED_STORAGE
    state = observation.verifier_state()
    assert state["storage_used"] == 120
    assert state["storage_capacity"] == 120
    assert state["storage_confidence"] == 1.0


def test_size_mismatch_fails_without_consuming_frame_id() -> None:
    observer = AdbScreenshotObserver(
        runner=FakeRunner(make_png(10, 6), make_png(8, 6)),
        clock=SequenceClock(1.0, 2.0),
        expected_size=(8, 6),
    )

    with pytest.raises(ObservationBuildError, match="frozen baseline"):
        observer.observe()

    recovered = observer.observe()
    assert recovered.frame_id == 1
    assert recovered.captured_at == 2.0


def test_invalid_png_decode_is_reported_as_observation_error() -> None:
    observer = AdbScreenshotObserver(
        runner=FakeRunner(b"not-a-png"),
        clock=SequenceClock(1.0),
    )

    with pytest.raises(ObservationBuildError, match="decode ADB screenshot"):
        observer.observe()


def test_observer_rejects_non_positive_capture_clock() -> None:
    observer = AdbScreenshotObserver(
        runner=FakeRunner(make_png()),
        clock=SequenceClock(0.0),
    )

    with pytest.raises(ObservationBuildError, match="positive timestamp"):
        observer.observe()
