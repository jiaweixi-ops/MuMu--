from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Protocol

from PIL import Image, UnidentifiedImageError

from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    ScreenType,
    StorageCapacity,
    V0Policy,
)
from simcity_ai_mayor.device.adb import AdbRunner
from simcity_ai_mayor.runtime.orchestrator import Observation


class ObservationBuildError(RuntimeError):
    pass


class ScreenClassifier(Protocol):
    def classify(self, image: Image.Image) -> ScreenType: ...


class StorageReader(Protocol):
    def read(self, image: Image.Image, screen: ScreenType) -> StorageCapacity | None: ...


class FactoryReader(Protocol):
    def read(self, image: Image.Image, screen: ScreenType) -> FactoryState: ...


class AutomationStateResolver(Protocol):
    def resolve(
        self,
        *,
        screen: ScreenType,
        storage: StorageCapacity | None,
        factory_state: FactoryState,
    ) -> AutomationState: ...


class UnknownScreenClassifier:
    def classify(self, image: Image.Image) -> ScreenType:
        del image
        return ScreenType.UNKNOWN


class NullStorageReader:
    def read(self, image: Image.Image, screen: ScreenType) -> StorageCapacity | None:
        del image, screen
        return None


class UnknownFactoryReader:
    def read(self, image: Image.Image, screen: ScreenType) -> FactoryState:
        del image, screen
        return FactoryState.UNKNOWN


class ConservativeAutomationStateResolver:
    """Derive only states that are safe without game-specific heuristics."""

    def __init__(self, policy: V0Policy | None = None) -> None:
        self.policy = policy or V0Policy()

    def resolve(
        self,
        *,
        screen: ScreenType,
        storage: StorageCapacity | None,
        factory_state: FactoryState,
    ) -> AutomationState:
        if screen in {ScreenType.NETWORK_ERROR, ScreenType.UNKNOWN_POPUP, ScreenType.UNKNOWN}:
            return AutomationState.RECOVER
        if storage is not None and storage.confidence < self.policy.min_ocr_confidence:
            return AutomationState.WAIT_OCR_UNTRUSTED
        if factory_state is FactoryState.COMPLETED_STORAGE_BLOCKED:
            return AutomationState.BLOCKED_STORAGE
        if storage is not None and storage.free == 0:
            return AutomationState.STORAGE_FULL
        return AutomationState.OBSERVE


@dataclass(frozen=True, slots=True)
class ObserverFrameMetadata:
    width: int
    height: int
    mode: str

    def as_state(self) -> Mapping[str, Any]:
        return {
            "frame_width": self.width,
            "frame_height": self.height,
            "frame_mode": self.mode,
        }


@dataclass(slots=True)
class AdbScreenshotObserver:
    runner: AdbRunner
    screen_classifier: ScreenClassifier = field(default_factory=UnknownScreenClassifier)
    storage_reader: StorageReader = field(default_factory=NullStorageReader)
    factory_reader: FactoryReader = field(default_factory=UnknownFactoryReader)
    state_resolver: AutomationStateResolver = field(
        default_factory=ConservativeAutomationStateResolver
    )
    clock: Callable[[], float] = time.monotonic
    expected_size: tuple[int, int] | None = None

    def __post_init__(self) -> None:
        if self.expected_size is not None:
            width, height = self.expected_size
            if width <= 0 or height <= 0:
                raise ValueError("expected_size dimensions must be > 0")
        self._frame_id = 0

    @property
    def device_id(self) -> str:
        return self.runner.device_id

    def observe(self) -> Observation:
        png = self.runner.screenshot_png()
        image = self._decode_png(png)
        captured_at = self.clock()
        if captured_at <= 0:
            raise ObservationBuildError("observer clock must return a positive timestamp")

        self._validate_size(image)
        frame_id = self._frame_id + 1
        screen = self.screen_classifier.classify(image)
        storage = self.storage_reader.read(image, screen)
        factory_state = self.factory_reader.read(image, screen)
        automation_state = self.state_resolver.resolve(
            screen=screen,
            storage=storage,
            factory_state=factory_state,
        )

        metadata = ObserverFrameMetadata(image.width, image.height, image.mode)
        observation = Observation(
            device_id=self.device_id,
            frame_id=frame_id,
            captured_at=captured_at,
            screen=screen,
            automation_state=automation_state,
            storage=storage,
            factory_state=factory_state,
            state=metadata.as_state(),
        )
        self._frame_id = frame_id
        return observation

    def _decode_png(self, png: bytes) -> Image.Image:
        try:
            with Image.open(BytesIO(png)) as decoded:
                decoded.load()
                if decoded.format != "PNG":
                    raise ObservationBuildError(
                        f"ADB screenshot decoded as {decoded.format!r}, expected 'PNG'"
                    )
                return decoded.convert("RGB")
        except ObservationBuildError:
            raise
        except (OSError, UnidentifiedImageError) as exc:
            raise ObservationBuildError("failed to decode ADB screenshot PNG") from exc

    def _validate_size(self, image: Image.Image) -> None:
        if self.expected_size is None:
            return
        actual = (image.width, image.height)
        if actual != self.expected_size:
            raise ObservationBuildError(
                f"ADB screenshot size {actual} != frozen baseline {self.expected_size}"
            )
