from __future__ import annotations

import argparse
import hashlib
import json
import os
import struct
import sys
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from simcity_ai_mayor.device.adb import AdbRunner, AdbTimeout


@dataclass(frozen=True, slots=True)
class ProbeResult:
    device_id: str
    probed_at: str
    wm_size: str
    wm_density: str
    screenshot_samples: int
    screenshot_sizes: list[tuple[int, int]]
    screenshot_size_stable: bool
    unique_frame_hashes: int
    game_version: str | None
    windows_session_name: str | None
    rdp_session_detected: bool
    manual_checks_required: list[str]


def png_size(data: bytes) -> tuple[int, int]:
    if len(data) < 24 or data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("ADB screencap did not return a valid PNG")
    return struct.unpack(">II", data[16:24])


def run_probe(
    *,
    device_id: str,
    adb_path: str,
    samples: int,
    package_name: str | None,
) -> ProbeResult:
    adb = AdbRunner(device_id, adb_path=adb_path)
    sizes: list[tuple[int, int]] = []
    hashes: set[str] = set()

    for _ in range(samples):
        png = adb.screenshot_png()
        sizes.append(png_size(png))
        hashes.add(hashlib.sha256(png).hexdigest())

    session_name = os.environ.get("SESSIONNAME")
    rdp = bool(session_name and session_name.upper().startswith("RDP"))
    version = adb.package_version(package_name) if package_name else None

    return ProbeResult(
        device_id=device_id,
        probed_at=datetime.now(UTC).isoformat(),
        wm_size=adb.wm_size(),
        wm_density=adb.wm_density(),
        screenshot_samples=samples,
        screenshot_sizes=sizes,
        screenshot_size_stable=len(set(sizes)) == 1,
        unique_frame_hashes=len(hashes),
        game_version=version,
        windows_session_name=session_name,
        rdp_session_detected=rdp,
        manual_checks_required=[
            "MuMu window move keeps ADB coordinates stable",
            "MuMu window resize does not alter frozen Android framebuffer",
            "Windows DPI change does not alter ADB screenshot geometry",
            "minimize MuMu and verify screencap remains valid",
            "background MuMu and verify game/screencap behavior",
            "disconnect/reconnect RDP and verify screencap is not black/stale",
            "record MuMu version and Android version",
            "record screen orientation and auto-rotate state",
        ],
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Phase -1a MuMu/ADB environment probe")
    parser.add_argument("--device-id", required=True)
    parser.add_argument("--adb", default="adb", help="path to adb executable")
    parser.add_argument("--samples", type=int, default=100)
    parser.add_argument("--package", dest="package_name")
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

    try:
        result = run_probe(
            device_id=args.device_id,
            adb_path=args.adb,
            samples=args.samples,
            package_name=args.package_name,
        )
    except AdbTimeout as exc:
        print(f"probe failed: {exc}", file=sys.stderr)
        return 2

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    if not result.screenshot_size_stable:
        print("Gate -1a FAIL: screenshot dimensions are not stable", file=sys.stderr)
        return 3

    print(json.dumps(asdict(result), ensure_ascii=False, indent=2))
    print(f"baseline draft written to: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
