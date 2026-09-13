from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


class AdbError(RuntimeError):
    pass


class AdbTimeout(AdbError):
    pass


@dataclass(frozen=True, slots=True)
class AdbResult:
    args: tuple[str, ...]
    returncode: int
    stdout: bytes
    stderr: bytes

    def stdout_text(self) -> str:
        return self.stdout.decode("utf-8", errors="replace").strip()

    def stderr_text(self) -> str:
        return self.stderr.decode("utf-8", errors="replace").strip()


class AdbRunner:
    """Low-level ADB process wrapper with hard command timeouts."""

    def __init__(
        self,
        device_id: str,
        *,
        adb_path: str | Path = "adb",
        timeout_seconds: float = 8.0,
    ) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        self.device_id = device_id
        self.adb_path = str(adb_path)
        self.timeout_seconds = timeout_seconds

    def run(
        self,
        args: Sequence[str],
        *,
        timeout_seconds: float | None = None,
        check: bool = True,
        device_scoped: bool = True,
    ) -> AdbResult:
        cmd = [self.adb_path]
        if device_scoped:
            cmd += ["-s", self.device_id]
        cmd += list(args)
        timeout = timeout_seconds or self.timeout_seconds

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            stdout, stderr = process.communicate()
            raise AdbTimeout(
                f"ADB command timed out after {timeout:.1f}s: {' '.join(cmd)}; "
                f"stderr={stderr.decode('utf-8', errors='replace').strip()}"
            ) from exc

        result = AdbResult(tuple(cmd), process.returncode, stdout, stderr)
        if check and result.returncode != 0:
            raise AdbError(
                f"ADB command failed ({result.returncode}): {' '.join(cmd)}; "
                f"stderr={result.stderr_text()}"
            )
        return result

    def shell(self, *args: str, timeout_seconds: float | None = None) -> AdbResult:
        return self.run(["shell", *args], timeout_seconds=timeout_seconds)

    def tap(self, x: int, y: int) -> None:
        self.shell("input", "tap", str(x), str(y))

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 350) -> None:
        self.shell(
            "input",
            "swipe",
            str(x1),
            str(y1),
            str(x2),
            str(y2),
            str(duration_ms),
        )

    def back(self) -> None:
        self.shell("input", "keyevent", "KEYCODE_BACK")

    def screenshot_png(self) -> bytes:
        return self.run(["exec-out", "screencap", "-p"], timeout_seconds=12.0).stdout

    def wm_size(self) -> str:
        return self.shell("wm", "size").stdout_text()

    def wm_density(self) -> str:
        return self.shell("wm", "density").stdout_text()

    def package_version(self, package_name: str) -> str | None:
        result = self.shell("dumpsys", "package", package_name)
        for line in result.stdout_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("versionName="):
                return stripped.split("=", 1)[1].strip()
        return None

    def recover_adb_server(self) -> None:
        """Process-level recovery; caller must serialize this with all other ADB writes."""
        self.run(["kill-server"], device_scoped=False, check=False, timeout_seconds=5.0)
        self.run(["start-server"], device_scoped=False, timeout_seconds=8.0)
