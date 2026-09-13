from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path


class EmergencyStopLatched(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EmergencyStopChannels:
    in_process: bool
    stop_flag: bool
    hotkey: bool
    hotkey_reason: str | None = None


class EmergencyStop:
    """Latched emergency stop with explicit channel-health reporting."""

    HOTKEY_ID = 0x5343
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    VK_F12 = 0x7B
    WM_HOTKEY = 0x0312

    def __init__(
        self,
        *,
        data_root: str | Path,
        stop_flag_relative: str | Path = "runtime/stop.flag",
    ) -> None:
        root = Path(data_root).expanduser().resolve()
        relative = Path(stop_flag_relative)
        if relative.is_absolute():
            raise ValueError("stop_flag_relative must be relative to data_root")
        self.stop_flag = (root / relative).resolve()
        self._event = threading.Event()
        self._shutdown = threading.Event()
        self._threads: list[threading.Thread] = []
        self._hotkey_ready = threading.Event()
        self._hotkey_available = False
        self._hotkey_reason: str | None = None

    @property
    def is_set(self) -> bool:
        return self._event.is_set()

    def trigger(self) -> None:
        self._event.set()

    def reset(self) -> None:
        self._event.clear()
        try:
            self.stop_flag.unlink(missing_ok=True)
        except OSError:
            pass

    def check(self) -> None:
        if self.is_set:
            raise EmergencyStopLatched("EMERGENCY_STOP is latched")

    def start_watchers(self) -> EmergencyStopChannels:
        self.stop_flag.parent.mkdir(parents=True, exist_ok=True)

        flag_thread = threading.Thread(
            target=self._watch_flag,
            name="emergency-stop-flag",
            daemon=True,
        )
        flag_thread.start()
        self._threads.append(flag_thread)

        if os.name == "nt":
            hotkey_thread = threading.Thread(
                target=self._watch_windows_hotkey,
                name="emergency-stop-hotkey",
                daemon=True,
            )
            hotkey_thread.start()
            self._threads.append(hotkey_thread)
            self._hotkey_ready.wait(timeout=1.0)
        else:
            self._hotkey_available = False
            self._hotkey_reason = "global hotkey is only available on Windows"
            self._hotkey_ready.set()

        return EmergencyStopChannels(
            in_process=True,
            stop_flag=True,
            hotkey=self._hotkey_available,
            hotkey_reason=self._hotkey_reason,
        )

    def close(self) -> None:
        self._shutdown.set()
        for thread in self._threads:
            thread.join(timeout=1.0)

    def _watch_flag(self) -> None:
        while not self._shutdown.is_set():
            if self.stop_flag.exists():
                self.trigger()
            time.sleep(0.25)

    def _watch_windows_hotkey(self) -> None:
        user32 = ctypes.windll.user32
        registered = bool(
            user32.RegisterHotKey(
                None,
                self.HOTKEY_ID,
                self.MOD_CONTROL | self.MOD_ALT,
                self.VK_F12,
            )
        )
        self._hotkey_available = registered
        if not registered:
            self._hotkey_reason = "RegisterHotKey failed; Ctrl+Alt+F12 may be occupied"
            self._hotkey_ready.set()
            return

        self._hotkey_ready.set()
        msg = wintypes.MSG()
        try:
            while not self._shutdown.is_set():
                result = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
                if (
                    result
                    and msg.message == self.WM_HOTKEY
                    and msg.wParam == self.HOTKEY_ID
                ):
                    self.trigger()
                time.sleep(0.05)
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
