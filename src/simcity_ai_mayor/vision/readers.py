from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image, ImageChops, ImageStat

from simcity_ai_mayor.core.models import FactoryState, ScreenType, StorageCapacity

_RATIO_RE = re.compile(r"(?<!\d)(\d{1,6})\s*[/／]\s*(\d{1,6})(?!\d)")


class VisionConfigError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Roi:
    x: int
    y: int
    width: int
    height: int

    def __post_init__(self) -> None:
        if self.x < 0 or self.y < 0:
            raise VisionConfigError("ROI x/y must be >= 0")
        if self.width <= 0 or self.height <= 0:
            raise VisionConfigError("ROI width/height must be > 0")

    @classmethod
    def from_sequence(cls, values: Sequence[int]) -> Roi:
        if len(values) != 4:
            raise VisionConfigError("ROI must contain [x, y, width, height]")
        return cls(*(int(value) for value in values))

    def crop(self, image: Image.Image) -> Image.Image:
        right = self.x + self.width
        bottom = self.y + self.height
        if right > image.width or bottom > image.height:
            raise VisionConfigError(
                f"ROI {(self.x, self.y, self.width, self.height)} exceeds "
                f"frame {(image.width, image.height)}"
            )
        return image.crop((self.x, self.y, right, bottom))


@dataclass(frozen=True, slots=True)
class TemplateAnchorSpec:
    roi: Roi
    template_path: Path
    threshold: float = 0.92
    weight: float = 1.0

    def __post_init__(self) -> None:
        if not 0.0 <= self.threshold <= 1.0:
            raise VisionConfigError("template threshold must be between 0 and 1")
        if self.weight <= 0:
            raise VisionConfigError("template weight must be > 0")


@dataclass(frozen=True, slots=True)
class ScreenTemplateRule:
    screen: ScreenType
    anchors: tuple[TemplateAnchorSpec, ...]

    def __post_init__(self) -> None:
        if not self.anchors:
            raise VisionConfigError("screen template rule requires at least one anchor")


@dataclass(frozen=True, slots=True)
class FactoryTemplateRule:
    state: FactoryState
    anchors: tuple[TemplateAnchorSpec, ...]

    def __post_init__(self) -> None:
        if not self.anchors:
            raise VisionConfigError("factory template rule requires at least one anchor")


@dataclass(frozen=True, slots=True)
class OcrFragment:
    text: str
    confidence: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("OCR confidence must be between 0 and 1")


class OcrBackend(Protocol):
    def recognize(self, image: Image.Image) -> Sequence[OcrFragment]: ...


class RapidOcrBackend:
    """Lazy RapidOCR adapter so the base package does not require vision extras."""

    def __init__(self) -> None:
        try:
            import numpy as np
            from rapidocr_onnxruntime import RapidOCR
        except ImportError as exc:
            raise RuntimeError(
                'RapidOCR is not installed; install with: pip install -e ".[vision]"'
            ) from exc
        self._np = np
        self._engine = RapidOCR()

    def recognize(self, image: Image.Image) -> Sequence[OcrFragment]:
        result, _elapsed = self._engine(self._np.asarray(image.convert("RGB")))
        if not result:
            return ()
        fragments: list[OcrFragment] = []
        for item in result:
            if len(item) < 3:
                continue
            text = str(item[1]).strip()
            if not text:
                continue
            try:
                confidence = float(item[2])
            except (TypeError, ValueError):
                continue
            fragments.append(OcrFragment(text, max(0.0, min(1.0, confidence))))
        return tuple(fragments)


class StorageRoiReader:
    """Read one fixed storage used/capacity ROI, e.g. `114/120`."""

    def __init__(
        self,
        *,
        roi: Roi,
        backend: OcrBackend,
        enabled_screens: frozenset[ScreenType] | None = None,
    ) -> None:
        self.roi = roi
        self.backend = backend
        self.enabled_screens = enabled_screens or frozenset(
            {ScreenType.FACTORY, ScreenType.STORAGE}
        )

    def read(self, image: Image.Image, screen: ScreenType) -> StorageCapacity | None:
        if screen not in self.enabled_screens:
            return None
        fragments = tuple(self.backend.recognize(self.roi.crop(image)))
        if not fragments:
            return None

        candidates: list[tuple[int, int, float]] = []
        for fragment in fragments:
            parsed = self._parse_ratio(fragment.text)
            if parsed is not None:
                candidates.append((*parsed, fragment.confidence))

        joined = " ".join(fragment.text for fragment in fragments)
        joined_ratio = self._parse_ratio(joined)
        if joined_ratio is not None:
            confidence = min(fragment.confidence for fragment in fragments)
            candidates.append((*joined_ratio, confidence))

        valid = [
            candidate
            for candidate in candidates
            if candidate[1] > 0 and 0 <= candidate[0] <= candidate[1]
        ]
        if not valid:
            return None
        used, capacity, confidence = max(valid, key=lambda item: item[2])
        return StorageCapacity(used=used, capacity=capacity, confidence=confidence)

    @staticmethod
    def _parse_ratio(text: str) -> tuple[int, int] | None:
        match = _RATIO_RE.search(text)
        if match is None:
            return None
        return int(match.group(1)), int(match.group(2))


class _LoadedAnchor:
    def __init__(self, spec: TemplateAnchorSpec) -> None:
        self.spec = spec
        path = spec.template_path.expanduser().resolve()
        if not path.exists():
            raise VisionConfigError(f"template does not exist: {path}")
        with Image.open(path) as image:
            template = image.convert("L")
            template.load()
        expected = (spec.roi.width, spec.roi.height)
        if template.size != expected:
            raise VisionConfigError(
                f"template {path} size {template.size} != ROI size {expected}"
            )
        self.template = template

    def score(self, image: Image.Image) -> float:
        sample = self.spec.roi.crop(image).convert("L")
        diff = ImageChops.difference(sample, self.template)
        mean = float(ImageStat.Stat(diff).mean[0])
        return max(0.0, min(1.0, 1.0 - mean / 255.0))


class _LoadedRule:
    def __init__(self, value: ScreenType | FactoryState, anchors: Sequence[TemplateAnchorSpec]) -> None:
        self.value = value
        self.anchors = tuple(_LoadedAnchor(anchor) for anchor in anchors)

    def score(self, image: Image.Image) -> float | None:
        weighted = 0.0
        total_weight = 0.0
        for anchor in self.anchors:
            score = anchor.score(image)
            if score < anchor.spec.threshold:
                return None
            weighted += score * anchor.spec.weight
            total_weight += anchor.spec.weight
        return weighted / total_weight


class FixedAnchorScreenClassifier:
    """Classify a screen only when all configured anchors for one rule match."""

    def __init__(
        self,
        rules: Sequence[ScreenTemplateRule],
        *,
        ambiguity_margin: float = 0.02,
    ) -> None:
        if not rules:
            raise VisionConfigError("at least one screen rule is required")
        if not 0.0 <= ambiguity_margin <= 1.0:
            raise VisionConfigError("ambiguity_margin must be between 0 and 1")
        self._rules = tuple(_LoadedRule(rule.screen, rule.anchors) for rule in rules)
        self.ambiguity_margin = ambiguity_margin

    def classify(self, image: Image.Image) -> ScreenType:
        candidates: list[tuple[float, ScreenType]] = []
        for rule in self._rules:
            score = rule.score(image)
            if score is not None:
                candidates.append((score, ScreenType(rule.value)))
        if not candidates:
            return ScreenType.UNKNOWN
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < self.ambiguity_margin:
            return ScreenType.UNKNOWN
        return candidates[0][1]


class FixedAnchorFactoryReader:
    """Factory-state reader using fixed ROI template anchors."""

    def __init__(
        self,
        rules: Sequence[FactoryTemplateRule],
        *,
        ambiguity_margin: float = 0.02,
    ) -> None:
        if not rules:
            raise VisionConfigError("at least one factory rule is required")
        if not 0.0 <= ambiguity_margin <= 1.0:
            raise VisionConfigError("ambiguity_margin must be between 0 and 1")
        self._rules = tuple(_LoadedRule(rule.state, rule.anchors) for rule in rules)
        self.ambiguity_margin = ambiguity_margin

    def read(self, image: Image.Image, screen: ScreenType) -> FactoryState:
        if screen is not ScreenType.FACTORY:
            return FactoryState.UNKNOWN
        candidates: list[tuple[float, FactoryState]] = []
        for rule in self._rules:
            score = rule.score(image)
            if score is not None:
                candidates.append((score, FactoryState(rule.value)))
        if not candidates:
            return FactoryState.UNKNOWN
        candidates.sort(key=lambda item: item[0], reverse=True)
        if len(candidates) > 1 and candidates[0][0] - candidates[1][0] < self.ambiguity_margin:
            return FactoryState.UNKNOWN
        return candidates[0][1]
