from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import struct
import sys
import time
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from PIL import Image, ImageStat

from simcity_ai_mayor.device.adb import AdbError, AdbRunner


@dataclass(frozen=True, slots=True)
class ProbeResult:
    device_id: str
    probed_at: str
    wm_size: str
    wm_density: str
    android_version: str
    android_sdk: str
    orientation_matches: list[str]
    screenshot_samples: int
    screenshot_sizes: list[tuple[int, int]]
    screenshot_size_stable: bool
    first_mismatch_index: int | None
    mismatched_sizes: list[tuple[int, int]]
    unique_frame_hashes: int
    watch_seconds: float
    watch_samples: int
    longest_identical_frame_run: int
    stale_frame_ratio: float
    black_frame_ratio: float
    game_version: str
    windows_session_name: str | None
    rdp_session_detected: bool
    manual_checks_required: list[str]


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("ADB screencap did not return a valid PNG")
    return struct.unpack(">II", data[16:24])


def is_black_frame(data: bytes) -> bool:
    with Image.open(io.BytesIO(data)) as image:
        gray = image.convert("L").resize((32, 32))
        stats = ImageStat.Stat(gray)
        mean = float(stats.mean[0])
        extrema = gray.getextrema()
        return mean <= 2.0 and extrema[1] <= 5


def _orientation_matches(dump: str) -> list[str]:
    matches: list[str] = []
    for line in dump.splitlines():
        lowered = line.lower()
        if "orientation" in lowered or "rotation" in lowered:
            matches.append(line.strip())
    return matches[:20]


def _mismatch_details(
    sizes: list[tuple[int, int]],
) -> tuple[int | None, list[tuple[int, int]]]:
    if not sizes:
        return None, []
    expected = sizes[0]
    for index, size in enumerate(sizes[1:], start=1):
        if size != expected:
            return index, sorted(set(size for size in sizes if size != expected))
    return None, []


def _watch_frames(
    adb: AdbRunner,
    watch_seconds: float,
    interval_seconds: float = 2.0,
) -> tuple[int, int, float, float]:
    if watch_seconds <= 0:
        return 0, 0, 0.0, 0.0

    deadline = time.monotonic() + watch_seconds
    hashes: list[str] = []
    black = 0
    while time.monotonic() < deadline:
        frame = adb.screenshot_png()
        hashes.append(hashlib.sha256(frame).hexdigest())
        black += int(is_black_frame(frame))
        remaining = deadline - time.monotonic()
        if remaining > 0:
            time.sleep(min(interval_seconds, remaining))

    if not hashes:
        return 0, 0, 0.0, 0.0

    longest = 1
    current = 1
    repeated = 0
    for previous, current_hash in zip(hashes, hashes[1:], strict=False):
        if previous == current_hash:
            current += 1
            repeated += 1
            longest = max(longest, current)
        else:
            current = 1

    stale_ratio = repeated / max(1, len(hashes) - 1)
    black_ratio = black / len(hashes)
    return len(hashes), longest, stale_ratio, black_ratio


def run_probe(
    *,
    device_id: str,
    adb_path: str,
    samples: int,
    package_name: str,
    watch_seconds: float,
) -> ProbeResult:
    adb = AdbRunner(device_id, adb_path=adb_path)
    sizes: list[tuple[int, int]] = []
    hashes: set[str] = set()

    for _ in range(samples):
        png = adb.screenshot_png()
        sizes.append(png_size(png))
        hashes.add(hashlib.sha256(png).hexdigest())

    mismatch_index, mismatched_sizes = _mismatch_details(sizes)
    session_name = os.environ.get("SESSIONNAME")
    rdp = bool(session_name and session_name.upper().startswith("RDP"))
    version = adb.package_version(package_name)
    if not version:
        raise AdbError(f"game version not found for package {package_name!r}")

    watch_samples, longest_run, stale_ratio, black_ratio = _watch_frames(
        adb,
        watch_seconds,
    )

    return ProbeResult(
        device_id=device_id,
        probed_at=datetime.now(UTC).isoformat(),
        wm_size=adb.wm_size(),
        wm_density=adb.wm_density(),
        android_version=adb.android_version(),
        android_sdk=adb.android_sdk(),
        orientation_matches=_orientation_matches(adb.orientation_dump()),
        screenshot_samples=samples,
        screenshot_sizes=sizes,
        screenshot_size_stable=mismatch_index is None,
        first_mismatch_index=mismatch_index,
        mismatched_sizes=mismatched_sizes,
        unique_frame_hashes=len(hashes),
        watch_seconds=watch_seconds,
        watch_samples=watch_samples,
        longest_identical_frame_run=longest_run,
        stale_frame_ratio=stale_ratio,
        black_frame_ratio=black_ratio,
        game_version=version,
        windows_session_name=session_name,
        rdp_session_detected=rdp,
        manual_checks_required=[
            "record MuMu application version",
            "move/resize MuMu and verify ADB coordinates/framebuffer remain stable",
            "change Windows DPI and verify ADB screenshot geometry",
            "disconnect/reconnect RDP during --watch-seconds and review freeze/black ratios",
            "verify game behavior while MuMu is minimized/backgrounded",
        ],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase -1a MuMu/ADB environment probe")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--adb", default="adb", help="path to adb executable")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--package", dest="package_name", required=True)
    parser.add_argument("--watch-seconds", type=float, default=0.0)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/phase_minus1/ENV_BASELINE_DRAFT.json"),
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.samples < 1:
        raise SystemExit("--samples must be >= 1")
    if args.watch_seconds < 0:
        raise SystemExit("--watch-seconds must be >= 0")

    try:
        result = run_probe(
            device_id=args.device_id,
            adb_path=args.adb,
            samples=args.samples,
            package_name=args.package_name,
            watch_seconds=args.watch_seconds,
        )
    except AdbError as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not result.screenshot_size_stable:
        print(
            "Gate -1a FAIL: screenshot dimensions are not stable; "
            f"first_mismatch_index={result.first_mismatch_index}, "
            f"mismatched_sizes={result.mismatched_sizes}",
            file=sys.stderr,
        )
        return 3

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    print(f"baseline draft written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
