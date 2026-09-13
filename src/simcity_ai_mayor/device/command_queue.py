from __future__ import annotations

import queue
import threading
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable


class CommandQueueClosed(RuntimeError):
    pass


@dataclass(slots=True)
class _Command:
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    future: Future[Any]


class DeviceCommandQueue:
    """One writer queue per device_id.

    All ADB input and recovery commands for the same device must pass through this
    queue. Guardian may request cancellation, but it must not issue concurrent ADB
    writes on its own.
    """

    def __init__(self, device_id: str) -> None:
        self.device_id = device_id
        self._queue: queue.Queue[_Command | None] = queue.Queue()
        self._cancel_requested = threading.Event()
        self._closed = threading.Event()
        self._worker = threading.Thread(
            target=self._run,
            name=f"adb-writer-{device_id}",
            daemon=True,
        )
        self._worker.start()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def clear_cancel(self) -> None:
        self._cancel_requested.clear()

    def submit(self, fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Future[Any]:
        if self._closed.is_set():
            raise CommandQueueClosed(f"command queue for {self.device_id} is closed")
        future: Future[Any] = Future()
        self._queue.put(_Command(fn, args, kwargs, future))
        return future

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command is None:
                self._queue.task_done()
                return
            try:
                if self._cancel_requested.is_set():
                    command.future.cancel()
                elif not command.future.set_running_or_notify_cancel():
                    pass
                else:
                    command.future.set_result(command.fn(*command.args, **command.kwargs))
            except BaseException as exc:  # propagate worker failures to caller
                command.future.set_exception(exc)
            finally:
                self._queue.task_done()

    def drain(self) -> None:
        self._queue.join()

    def close(self) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        self._queue.put(None)
        self._worker.join(timeout=5.0)
