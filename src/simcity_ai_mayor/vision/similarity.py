from __future__ import annotations

from math import fsum, sqrt

from PIL import Image


def zero_mean_ncc(sample: Image.Image, template: Image.Image) -> float:
    """Return zero-mean normalized cross-correlation in the [0, 1] range.

    Negative correlation is clamped to zero because template matching treats it as a
    definite non-match. Constant patches are mathematically degenerate; they only match
    when their grayscale bytes are exactly equal.
    """

    sample_gray = sample.convert("L")
    template_gray = template.convert("L")
    if sample_gray.size != template_gray.size:
        raise ValueError(
            f"sample size {sample_gray.size} != template size {template_gray.size}"
        )

    sample_pixels = tuple(float(value) for value in sample_gray.tobytes())
    template_pixels = tuple(float(value) for value in template_gray.tobytes())
    if not sample_pixels:
        return 0.0

    sample_mean = fsum(sample_pixels) / len(sample_pixels)
    template_mean = fsum(template_pixels) / len(template_pixels)
    sample_centered = tuple(value - sample_mean for value in sample_pixels)
    template_centered = tuple(value - template_mean for value in template_pixels)
    sample_energy = fsum(value * value for value in sample_centered)
    template_energy = fsum(value * value for value in template_centered)

    if sample_energy == 0.0 or template_energy == 0.0:
        return 1.0 if sample_pixels == template_pixels else 0.0

    numerator = fsum(
        sample_value * template_value
        for sample_value, template_value in zip(
            sample_centered,
            template_centered,
            strict=True,
        )
    )
    correlation = numerator / sqrt(sample_energy * template_energy)
    return max(0.0, min(1.0, correlation))
