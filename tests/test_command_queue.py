import pytest

from simcity_ai_mayor.device.adb import AdbRunner
from simcity_ai_mayor.device.command_queue import (
    CommandCancelled,
    CommandQueueClosed,
    DeviceCommandQueue,
    QueueBoundAdbWriter,
)


def test_device_command_queue_preserves_order_and_reports_no_violation() -> None:
    command_queue = DeviceCommandQueue("mumu-0")
    seen: list[int] = []

    futures = [command_queue.submit(seen.append, value) for value in range(20)]
    for future in futures:
        future.result(timeout=2)

    assert seen == list(range(20))
    assert command_queue.order_violation_count == 0
    command_queue.close()


def test_cancel_rejects_new_work_explicitly() -> None:
    command_queue = DeviceCommandQueue("mumu-0")
    command_queue.request_cancel()
    with pytest.raises(CommandCancelled):
        command_queue.submit(lambda: 123)
    command_queue.close()


def test_close_rejects_future_submissions() -> None:
    command_queue = DeviceCommandQueue("mumu-0")
    command_queue.close()
    with pytest.raises(CommandQueueClosed):
        command_queue.submit(lambda: 123)


def test_queue_bound_writer_is_the_supported_input_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = AdbRunner("mumu-0")
    command_queue = DeviceCommandQueue("mumu-0")
    writer = QueueBoundAdbWriter(runner, command_queue)
    seen: list[tuple[tuple[str, ...], dict[str, object]]] = []

    def fake_shell(*args: str, **kwargs: object) -> None:
        seen.append((args, kwargs))

    monkeypatch.setattr(runner, "shell", fake_shell)
    writer.tap(100, 200).result(timeout=2)

    assert seen[0][0] == ("input", "tap", "100", "200")
    assert "_write_capability" in seen[0][1]
    command_queue.close()
