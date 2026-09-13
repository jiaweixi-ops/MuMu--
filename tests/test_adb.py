from __future__ import annotations

import subprocess
from collections import deque

import pytest

from simcity_ai_mayor.device.adb import PNG_SIGNATURE, AdbRunner, AdbTimeout


class FakeProcess:
    def __init__(
        self,
        cmd: list[str],
        *,
        stdout: bytes = b"",
        stderr: bytes = b"",
        returncode: int = 0,
        timeout_once: bool = False,
    ) -> None:
        self.cmd = cmd
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode
        self._timeout_once = timeout_once
        self.killed = False

    def communicate(self, timeout: float | None = None) -> tuple[bytes, bytes]:
        if self._timeout_once:
            self._timeout_once = False
            raise subprocess.TimeoutExpired(self.cmd, timeout)
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.killed = True


def test_recover_adb_server_reconnects_tcp_device(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[list[str]] = []
    outputs = deque(
        [
            (b"", b"", 0),
            (b"", b"", 0),
            (b"connected to 127.0.0.1:7555\n", b"", 0),
            (b"List of devices attached\n127.0.0.1:7555\tdevice\n", b"", 0),
        ]
    )

    def fake_popen(cmd: list[str], **_: object) -> FakeProcess:
        calls.append(cmd)
        stdout, stderr, returncode = outputs.popleft()
        return FakeProcess(cmd, stdout=stdout, stderr=stderr, returncode=returncode)

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adb = AdbRunner("127.0.0.1:7555")
    adb.recover_adb_server(wait_seconds=1.0)

    flattened = [" ".join(call) for call in calls]
    assert any("kill-server" in call for call in flattened)
    assert any("start-server" in call for call in flattened)
    assert any("connect 127.0.0.1:7555" in call for call in flattened)
    assert any(call.endswith("devices") for call in flattened)


def test_timeout_kills_process(monkeypatch: pytest.MonkeyPatch) -> None:
    process = FakeProcess(["adb", "devices"], timeout_once=True)

    def fake_popen(cmd: list[str], **_: object) -> FakeProcess:
        process.cmd = cmd
        return process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    adb = AdbRunner("mumu-0", timeout_seconds=0.01)
    with pytest.raises(AdbTimeout):
        adb.run(["devices"], device_scoped=False)
    assert process.killed


def test_screenshot_retries_invalid_png(monkeypatch: pytest.MonkeyPatch) -> None:
    valid_png = (
        PNG_SIGNATURE
        + b"\x00\x00\x00\x0dIHDR"
        + (1280).to_bytes(4, "big")
        + (720).to_bytes(4, "big")
        + b"x" * 16
    )
    outputs = deque([b"bad", valid_png])

    def fake_popen(cmd: list[str], **_: object) -> FakeProcess:
        return FakeProcess(cmd, stdout=outputs.popleft())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    adb = AdbRunner("mumu-0", screenshot_retries=1)
    assert adb.screenshot_png().startswith(PNG_SIGNATURE)
