from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import os
import threading
import time
from pathlib import Path


class EmergencyStop:
    """Three-channel emergency stop.

    1. In-process event for normal UI STOP.
    2. Out-of-band stop.flag watcher.
    3. Windows global hotkey Ctrl+Alt+F12 when available.

    Once tripped, the stop remains latched until reset() is explicitly called.
    If the process itself is unresponsive, physically closing MuMu remains the final
    out-of-process safety fallback.
    """

    HOTKEY_ID = 0x5343
    MOD_ALT = 0x0001
    MOD_CONTROL = 0x0002
    VK_F12 = 0x7B
    WM_HOTKEY = 0x0312

    def __init__(self, stop_flag: str | Path = "runtime/stop.flag") -> None:
        self.stop_flag = Path(stop_flag)
        self._event = threading.Event()
        self._shutdown = threading.Event()
        self._threads: list[threading.Thread] = []

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
            raise RuntimeError("EMERGENCY_STOP is latched")

    def start_watchers(self) -> None:
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
        if not user32.RegisterHotKey(
            None,
            self.HOTKEY_ID,
            self.MOD_CONTROL | self.MOD_ALT,
            self.VK_F12,
        ):
            return

        msg = wintypes.MSG()
        try:
            while not self._shutdown.is_set():
                result = user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1)
                if result and msg.message == self.WM_HOTKEY and msg.wParam == self.HOTKEY_ID:
                    self.trigger()
                time.sleep(0.05)
        finally:
            user32.UnregisterHotKey(None, self.HOTKEY_ID)
