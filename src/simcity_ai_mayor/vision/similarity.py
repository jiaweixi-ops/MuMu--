from __future__ import annotations

from dataclasses import dataclass
from math import fsum, sqrt

from PIL import Image


@dataclass(frozen=True, slots=True)
class NccTemplateProfile:
    """Precomputed zero-mean NCC statistics for one grayscale template.

    The expensive template-side centering and energy calculation is performed once.
    Repeated callers only compute sample-side statistics. Scores are clamped to [0, 1]
    because negative correlation is a definite non-match for template detection.
    """

    size: tuple[int, int]
    pixels: tuple[float, ...]
    centered: tuple[float, ...]
    energy: float

    @classmethod
    def from_image(cls, template: Image.Image) -> NccTemplateProfile:
        template_gray = template.convert("L")
        pixels = tuple(float(value) for value in template_gray.tobytes())
        if not pixels:
            return cls(template_gray.size, (), (), 0.0)
        mean = fsum(pixels) / len(pixels)
        centered = tuple(value - mean for value in pixels)
        energy = fsum(value * value for value in centered)
        return cls(template_gray.size, pixels, centered, energy)

    def score(self, sample: Image.Image) -> float:
        sample_gray = sample.convert("L")
        if sample_gray.size != self.size:
            raise ValueError(f"sample size {sample_gray.size} != template size {self.size}")

        sample_pixels = tuple(float(value) for value in sample_gray.tobytes())
        if not sample_pixels or not self.pixels:
            return 0.0

        sample_mean = fsum(sample_pixels) / len(sample_pixels)
        sample_centered = tuple(value - sample_mean for value in sample_pixels)
        sample_energy = fsum(value * value for value in sample_centered)

        if sample_energy == 0.0 or self.energy == 0.0:
            return 1.0 if sample_pixels == self.pixels else 0.0

        numerator = fsum(
            sample_value * template_value
            for sample_value, template_value in zip(
                sample_centered,
                self.centered,
                strict=True,
            )
        )
        correlation = numerator / sqrt(sample_energy * self.energy)
        return max(0.0, min(1.0, correlation))


def zero_mean_ncc(sample: Image.Image, template: Image.Image) -> float:
    """Return zero-mean normalized cross-correlation in the [0, 1] range.

    This convenience wrapper is intended for one-off comparisons. Repeated matching
    against the same template should construct ``NccTemplateProfile`` once and reuse it.
    """

    return NccTemplateProfile.from_image(template).score(sample)
