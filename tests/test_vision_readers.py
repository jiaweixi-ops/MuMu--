from pathlib import Path

from PIL import Image

from simcity_ai_mayor.core.models import FactoryState, ScreenType, StorageCapacity
from simcity_ai_mayor.vision.readers import (
    FactoryTemplateRule,
    FixedAnchorFactoryReader,
    FixedAnchorScreenClassifier,
    OcrFragment,
    Roi,
    ScreenTemplateRule,
    StorageRoiReader,
    TemplateAnchorSpec,
)


class FakeOcr:
    def __init__(self, *fragments: OcrFragment) -> None:
        self.fragments = fragments

    def recognize(self, image: Image.Image):
        assert image.size == (80, 20)
        return self.fragments


def save_template(path: Path, value: int) -> None:
    Image.new("L", (10, 10), value).save(path)


def save_pattern_template(path: Path) -> Image.Image:
    image = Image.new("L", (10, 10))
    pixels = [40 + x * 8 + y * 3 for y in range(10) for x in range(10)]
    image.putdata(pixels)
    image.save(path)
    return image


def test_storage_reader_parses_fragmented_ratio() -> None:
    reader = StorageRoiReader(
        roi=Roi(0, 0, 80, 20),
        backend=FakeOcr(
            OcrFragment("114", 0.997),
            OcrFragment("/", 0.996),
            OcrFragment("120", 0.998),
        ),
    )
    image = Image.new("RGB", (100, 40), "white")

    storage = reader.read(image, ScreenType.FACTORY)

    assert storage == StorageCapacity(used=114, capacity=120, confidence=0.996)


def test_storage_reader_returns_none_for_invalid_capacity() -> None:
    reader = StorageRoiReader(
        roi=Roi(0, 0, 80, 20),
        backend=FakeOcr(OcrFragment("130/120", 1.0)),
    )

    assert reader.read(Image.new("RGB", (100, 40)), ScreenType.FACTORY) is None


def test_fixed_anchor_screen_classifier_uses_configured_template(tmp_path) -> None:
    template = tmp_path / "factory.png"
    save_template(template, 255)
    classifier = FixedAnchorScreenClassifier(
        [
            ScreenTemplateRule(
                ScreenType.FACTORY,
                (
                    TemplateAnchorSpec(
                        Roi(5, 5, 10, 10),
                        template,
                        threshold=0.99,
                    ),
                ),
            )
        ]
    )
    frame = Image.new("L", (30, 30), 0)
    frame.paste(Image.new("L", (10, 10), 255), (5, 5))

    assert classifier.classify(frame) is ScreenType.FACTORY


def test_fixed_anchor_screen_classifier_fails_closed_on_miss(tmp_path) -> None:
    template = tmp_path / "factory.png"
    save_template(template, 255)
    classifier = FixedAnchorScreenClassifier(
        [
            ScreenTemplateRule(
                ScreenType.FACTORY,
                (TemplateAnchorSpec(Roi(5, 5, 10, 10), template, threshold=0.99),),
            )
        ]
    )

    assert classifier.classify(Image.new("L", (30, 30), 0)) is ScreenType.UNKNOWN


def test_fixed_anchor_screen_classifier_tolerates_linear_brightness_change(
    tmp_path,
) -> None:
    template_path = tmp_path / "factory_pattern.png"
    template = save_pattern_template(template_path)
    classifier = FixedAnchorScreenClassifier(
        [
            ScreenTemplateRule(
                ScreenType.FACTORY,
                (
                    TemplateAnchorSpec(
                        Roi(5, 5, 10, 10),
                        template_path,
                        threshold=0.99,
                    ),
                ),
            )
        ]
    )

    shifted = Image.new("L", template.size)
    shifted.putdata(
        [min(255, int(value * 0.75 + 45)) for value in template.getdata()]
    )
    frame = Image.new("L", (30, 30), 0)
    frame.paste(shifted, (5, 5))

    assert classifier.classify(frame) is ScreenType.FACTORY


def test_factory_reader_only_runs_on_factory_screen(tmp_path) -> None:
    template = tmp_path / "idle.png"
    save_template(template, 128)
    reader = FixedAnchorFactoryReader(
        [
            FactoryTemplateRule(
                FactoryState.IDLE,
                (TemplateAnchorSpec(Roi(0, 0, 10, 10), template, threshold=0.99),),
            )
        ]
    )
    frame = Image.new("L", (20, 20), 128)

    assert reader.read(frame, ScreenType.CITY) is FactoryState.UNKNOWN
    assert reader.read(frame, ScreenType.FACTORY) is FactoryState.IDLE
