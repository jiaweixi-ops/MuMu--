from __future__ import annotations

import os
import struct
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


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
    """Low-level ADB wrapper with hard timeouts and TCP-device recovery."""

    def __init__(
        self,
        device_id: str,
        *,
        adb_path: str | Path = "adb",
        timeout_seconds: float = 8.0,
        screenshot_retries: int = 2,
    ) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be > 0")
        if screenshot_retries < 0:
            raise ValueError("screenshot_retries must be >= 0")
        self.device_id = device_id
        self.adb_path = str(adb_path)
        self.timeout_seconds = timeout_seconds
        self.screenshot_retries = screenshot_retries

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

        timeout = self.timeout_seconds if timeout_seconds is None else timeout_seconds
        if timeout <= 0:
            raise ValueError("timeout_seconds must be > 0")

        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=creationflags,
        )
        try:
            stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            try:
                stdout, stderr = process.communicate(timeout=2.0)
            except subprocess.TimeoutExpired:
                stdout = b""
                stderr = b"process pipes did not close after kill"
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

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 350,
    ) -> None:
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
        last_error: AdbError | None = None
        for attempt in range(self.screenshot_retries + 1):
            try:
                data = self.run(
                    ["exec-out", "screencap", "-p"],
                    timeout_seconds=12.0,
                ).stdout
                if len(data) < 24 or not data.startswith(PNG_SIGNATURE):
                    raise AdbError("ADB screencap returned an invalid PNG header")
                width, height = struct.unpack(">II", data[16:24])
                if width <= 0 or height <= 0:
                    raise AdbError("ADB screencap returned invalid PNG dimensions")
                return data
            except AdbError as exc:
                last_error = exc
                if attempt >= self.screenshot_retries:
                    break
                time.sleep(0.2 * (attempt + 1))
        assert last_error is not None
        raise last_error

    def wm_size(self) -> str:
        return self.shell("wm", "size").stdout_text()

    def wm_density(self) -> str:
        return self.shell("wm", "density").stdout_text()

    def getprop(self, name: str) -> str:
        return self.shell("getprop", name).stdout_text()

    def android_version(self) -> str:
        return self.getprop("ro.build.version.release")

    def android_sdk(self) -> str:
        return self.getprop("ro.build.version.sdk")

    def orientation_dump(self) -> str:
        return self.shell("dumpsys", "input").stdout_text()

    def package_version(self, package_name: str) -> str | None:
        result = self.shell("dumpsys", "package", package_name)
        for line in result.stdout_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("versionName="):
                return stripped.split("=", 1)[1].strip()
        return None

    def list_devices(self) -> dict[str, str]:
        result = self.run(["devices"], device_scoped=False)
        devices: dict[str, str] = {}
        for line in result.stdout_text().splitlines()[1:]:
            if not line.strip():
                continue
            parts = line.split()
            if len(parts) >= 2:
                devices[parts[0]] = parts[1]
        return devices

    def wait_for_device(self, wait_seconds: float = 10.0) -> None:
        if wait_seconds <= 0:
            raise ValueError("wait_seconds must be > 0")
        deadline = time.monotonic() + wait_seconds
        last_state: str | None = None
        while time.monotonic() < deadline:
            last_state = self.list_devices().get(self.device_id)
            if last_state == "device":
                return
            time.sleep(0.25)
        raise AdbError(
            f"device {self.device_id!r} did not become ready within "
            f"{wait_seconds:.1f}s; last_state={last_state!r}"
        )

    def recover_adb_server(
        self,
        *,
        connect_target: str | None = None,
        wait_seconds: float = 10.0,
    ) -> None:
        """Restart adb-server, reconnect a TCP MuMu target, then wait until ready."""
        target = connect_target
        if target is None and ":" in self.device_id:
            target = self.device_id

        self.run(
            ["kill-server"],
            device_scoped=False,
            check=False,
            timeout_seconds=5.0,
        )
        self.run(
            ["start-server"],
            device_scoped=False,
            timeout_seconds=8.0,
        )
        if target:
            self.run(
                ["connect", target],
                device_scoped=False,
                check=False,
                timeout_seconds=8.0,
            )
        self.wait_for_device(wait_seconds)
