import time
from pathlib import Path

import pytest

from simcity_ai_mayor.runtime.emergency_stop import (
    EmergencyStop,
    EmergencyStopLatched,
)


def test_stop_flag_latches_emergency_stop(tmp_path: Path) -> None:
    stop = EmergencyStop(data_root=tmp_path)
    channels = stop.start_watchers()
    assert channels.stop_flag
    assert stop.stop_flag.is_absolute()

    stop.stop_flag.write_text("stop", encoding="utf-8")
    deadline = time.time() + 2
    while time.time() < deadline and not stop.is_set:
        time.sleep(0.02)

    assert stop.is_set
    with pytest.raises(EmergencyStopLatched):
        stop.check()

    stop.reset()
    assert not stop.is_set
    assert not stop.stop_flag.exists()
    stop.close()


def test_hotkey_channel_status_is_explicit(tmp_path: Path) -> None:
    stop = EmergencyStop(data_root=tmp_path)
    channels = stop.start_watchers()
    assert isinstance(channels.hotkey, bool)
    if not channels.hotkey:
        assert channels.hotkey_reason
    stop.close()
