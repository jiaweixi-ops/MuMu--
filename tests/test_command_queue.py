from simcity_ai_mayor.device.command_queue import DeviceCommandQueue


def test_device_command_queue_preserves_order() -> None:
    queue = DeviceCommandQueue("mumu-0")
    seen: list[int] = []

    futures = [queue.submit(seen.append, value) for value in range(20)]
    for future in futures:
        future.result(timeout=2)

    assert seen == list(range(20))
    queue.close()


def test_cancel_blocks_queued_work_until_cleared() -> None:
    queue = DeviceCommandQueue("mumu-0")
    queue.request_cancel()
    future = queue.submit(lambda: 123)
    queue.drain()
    assert future.cancelled()

    queue.clear_cancel()
    next_future = queue.submit(lambda: 456)
    assert next_future.result(timeout=2) == 456
    queue.close()
