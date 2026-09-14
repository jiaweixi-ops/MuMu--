from PIL import Image

from simcity_ai_mayor.core.models import ScreenType, StorageCapacity
from simcity_ai_mayor.vision.storage_validation import (
    StorageValidationPolicy,
    ValidatedStorageReader,
)


class SequenceReader:
    def __init__(self, *readings: StorageCapacity) -> None:
        self.readings = list(readings)

    def read(self, image, screen):
        del image, screen
        return self.readings.pop(0)


def image() -> Image.Image:
    return Image.new("RGB", (10, 10), "white")


def test_repeated_engine_confidence_is_fused_into_measurement_confidence() -> None:
    reader = ValidatedStorageReader(
        SequenceReader(
            StorageCapacity(10, 100, 0.95),
            StorageCapacity(10, 100, 0.95),
        ),
        policy=StorageValidationPolicy(min_engine_confidence=0.80),
    )

    first = reader.read(image(), ScreenType.FACTORY)
    second = reader.read(image(), ScreenType.FACTORY)

    assert first is not None and first.confidence == 0.95
    assert second is not None and second.confidence > 0.99
    assert reader.diagnostics.raw_confidence == 0.95
    assert reader.diagnostics.measurement_confidence == second.confidence
    assert reader.diagnostics.stable_frames == 2


def test_unexplained_used_change_is_marked_untrusted() -> None:
    reader = ValidatedStorageReader(
        SequenceReader(
            StorageCapacity(10, 100, 1.0),
            StorageCapacity(11, 100, 1.0),
        )
    )

    assert reader.read(image(), ScreenType.FACTORY) is not None
    changed = reader.read(image(), ScreenType.FACTORY)

    assert changed == StorageCapacity(11, 100, 0.0)
    assert "unexplained" in reader.diagnostics.reason


def test_expected_collect_delta_is_accepted_once() -> None:
    reader = ValidatedStorageReader(
        SequenceReader(
            StorageCapacity(10, 100, 1.0),
            StorageCapacity(11, 100, 0.97),
            StorageCapacity(12, 100, 1.0),
        )
    )
    reader.read(image(), ScreenType.FACTORY)
    reader.expect_delta(1, 1)

    expected = reader.read(image(), ScreenType.FACTORY)
    unexpected = reader.read(image(), ScreenType.FACTORY)

    assert expected == StorageCapacity(11, 100, 0.97)
    assert unexpected == StorageCapacity(12, 100, 0.0)


def test_capacity_decrease_is_rejected() -> None:
    reader = ValidatedStorageReader(
        SequenceReader(
            StorageCapacity(80, 100, 1.0),
            StorageCapacity(70, 90, 1.0),
        )
    )
    reader.read(image(), ScreenType.FACTORY)

    changed = reader.read(image(), ScreenType.FACTORY)

    assert changed == StorageCapacity(70, 90, 0.0)
    assert "capacity decreased" in reader.diagnostics.reason


def test_decrease_from_full_storage_is_safe_manual_rebase() -> None:
    reader = ValidatedStorageReader(
        SequenceReader(
            StorageCapacity(100, 100, 1.0),
            StorageCapacity(90, 100, 0.98),
        )
    )
    reader.read(image(), ScreenType.FACTORY)

    cleared = reader.read(image(), ScreenType.FACTORY)

    assert cleared == StorageCapacity(90, 100, 0.98)
    assert "safe manual clear" in reader.diagnostics.reason
