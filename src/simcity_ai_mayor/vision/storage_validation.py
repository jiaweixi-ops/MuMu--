from __future__ import annotations

from dataclasses import dataclass
from math import prod
from typing import Protocol

from PIL import Image

from simcity_ai_mayor.core.models import ScreenType, StorageCapacity


class StorageReaderLike(Protocol):
    def read(self, image: Image.Image, screen: ScreenType) -> StorageCapacity | None: ...


@dataclass(frozen=True, slots=True)
class StorageValidationPolicy:
    min_engine_confidence: float = 0.80
    allow_capacity_increase: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.min_engine_confidence <= 1.0:
            raise ValueError("min_engine_confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class StorageValidationDiagnostics:
    raw_confidence: float | None
    measurement_confidence: float | None
    stable_frames: int
    reason: str


class ValidatedStorageReader:
    """Fuse repeated OCR readings and reject unexplained storage transitions.

    The wrapped reader provides engine confidence. This layer turns repeated identical
    readings into a measurement confidence using ``1 - product(1-c_i)``. A changed
    ``used`` value is accepted only when an expected action delta has been registered,
    or when a one-shot manual rebase was explicitly allowed. A decrease from a known
    full warehouse is also safe to rebase because it can only create free capacity.
    Capacity may increase when configured, but never silently decreases.
    """

    def __init__(
        self,
        reader: StorageReaderLike,
        *,
        policy: StorageValidationPolicy | None = None,
    ) -> None:
        self.reader = reader
        self.policy = policy or StorageValidationPolicy()
        self._baseline: StorageCapacity | None = None
        self._stable_key: tuple[int, int] | None = None
        self._stable_confidences: list[float] = []
        self._expected_delta: tuple[int, int] | None = None
        self._manual_rebase_allowed = False
        self._diagnostics = StorageValidationDiagnostics(None, None, 0, "no reading yet")

    @property
    def diagnostics(self) -> StorageValidationDiagnostics:
        return self._diagnostics

    def expect_delta(self, minimum: int, maximum: int) -> None:
        if minimum > maximum:
            raise ValueError("minimum storage delta must be <= maximum")
        self._expected_delta = (minimum, maximum)

    def clear_expectation(self) -> None:
        self._expected_delta = None

    def allow_manual_rebase_once(self) -> None:
        self._manual_rebase_allowed = True

    def read(self, image: Image.Image, screen: ScreenType) -> StorageCapacity | None:
        raw = self.reader.read(image, screen)
        if raw is None:
            self._diagnostics = StorageValidationDiagnostics(
                None,
                None,
                0,
                "storage OCR returned no valid reading",
            )
            return None
        if raw.confidence < self.policy.min_engine_confidence:
            self._expected_delta = None
            self._diagnostics = StorageValidationDiagnostics(
                raw.confidence,
                0.0,
                0,
                "engine confidence below validation floor",
            )
            return StorageCapacity(raw.used, raw.capacity, 0.0)

        expected_delta = self._expected_delta
        self._expected_delta = None
        reason = "stable reading"

        if self._baseline is not None:
            if raw.capacity < self._baseline.capacity:
                self._diagnostics = StorageValidationDiagnostics(
                    raw.confidence,
                    0.0,
                    0,
                    "capacity decreased without an allowed baseline reset",
                )
                return StorageCapacity(raw.used, raw.capacity, 0.0)
            if raw.capacity > self._baseline.capacity and not self.policy.allow_capacity_increase:
                self._diagnostics = StorageValidationDiagnostics(
                    raw.confidence,
                    0.0,
                    0,
                    "capacity increase is disabled by policy",
                )
                return StorageCapacity(raw.used, raw.capacity, 0.0)

            used_delta = raw.used - self._baseline.used
            if used_delta != 0:
                if self._delta_is_expected(used_delta, expected_delta):
                    reason = f"storage delta {used_delta} matched expected action"
                elif self._manual_rebase_allowed and used_delta < 0:
                    self._manual_rebase_allowed = False
                    reason = "storage decrease accepted by one-shot manual rebase"
                elif self._baseline.free == 0 and used_delta < 0:
                    reason = "storage decrease from full baseline treated as safe manual clear"
                else:
                    self._diagnostics = StorageValidationDiagnostics(
                        raw.confidence,
                        0.0,
                        0,
                        f"unexplained storage used delta {used_delta}",
                    )
                    return StorageCapacity(raw.used, raw.capacity, 0.0)
            elif raw.capacity > self._baseline.capacity:
                reason = "capacity increase accepted and requires fresh confidence fusion"

        measurement_confidence, stable_frames = self._accept(raw)
        self._diagnostics = StorageValidationDiagnostics(
            raw.confidence,
            measurement_confidence,
            stable_frames,
            reason,
        )
        return StorageCapacity(raw.used, raw.capacity, measurement_confidence)

    @staticmethod
    def _delta_is_expected(
        delta: int,
        expected: tuple[int, int] | None,
    ) -> bool:
        if expected is None:
            return False
        minimum, maximum = expected
        return minimum <= delta <= maximum

    def _accept(self, raw: StorageCapacity) -> tuple[float, int]:
        key = (raw.used, raw.capacity)
        if key != self._stable_key:
            self._stable_key = key
            self._stable_confidences = [raw.confidence]
        else:
            self._stable_confidences.append(raw.confidence)
        self._baseline = raw
        measurement_confidence = 1.0 - prod(1.0 - value for value in self._stable_confidences)
        return min(1.0, measurement_confidence), len(self._stable_confidences)
