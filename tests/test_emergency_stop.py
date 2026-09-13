import time
from pathlib import Path

from simcity_ai_mayor.runtime.emergency_stop import EmergencyStop


def test_stop_flag_latches_emergency_stop(tmp_path: Path) -> None:
    flag = tmp_path / "stop.flag"
    stop = EmergencyStop(flag)
    stop.start_watchers()
    flag.write_text("stop", encoding="utf-8")

    deadline = time.time() + 2
    while time.time() < deadline and not stop.is_set:
        time.sleep(0.02)

    assert stop.is_set
    stop.reset()
    assert not stop.is_set
    assert not flag.exists()
    stop.close()
