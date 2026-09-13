from __future__ import annotations

import queue
import threading
from collections.abc import Callable
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any

from simcity_ai_mayor.device.adb import _ADB_WRITE_CAPABILITY, AdbRunner


class CommandQueueClosed(RuntimeError):
    pass


class CommandQueueFull(RuntimeError):
    pass


class CommandCancelled(RuntimeError):
    pass


class CommandOrderViolation(RuntimeError):
    pass


@dataclass(slots=True)
class _Command:
    seq: int
    fn: Callable[..., Any]
    args: tuple[Any, ...]
    kwargs: dict[str, Any]
    future: Future[Any]


class DeviceCommandQueue:
    """Single ADB writer queue per device with bounded backlog and sequencing."""

    def __init__(self, device_id: str, *, maxsize: int = 128) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        if maxsize <= 0:
            raise ValueError("maxsize must be > 0")
        self.device_id = device_id
        self._queue: queue.Queue[_Command | None] = queue.Queue(maxsize=maxsize)
        self._cancel_requested = threading.Event()
        self._closed = threading.Event()
        self._seq_lock = threading.Lock()
        self._next_seq = 1
        self._last_completed_seq = 0
        self._order_violation_count = 0
        self._active_execution = False
        self._worker = threading.Thread(
            target=self._run,
            name=f"adb-writer-{device_id}",
            daemon=True,
        )
        self._worker.start()

    @property
    def cancel_requested(self) -> bool:
        return self._cancel_requested.is_set()

    @property
    def order_violation_count(self) -> int:
        return self._order_violation_count

    def request_cancel(self) -> None:
        self._cancel_requested.set()

    def clear_cancel(self) -> None:
        if self._closed.is_set():
            raise CommandQueueClosed(f"command queue for {self.device_id} is closed")
        self._cancel_requested.clear()

    def submit(
        self,
        fn: Callable[..., Any],
        /,
        *args: Any,
        **kwargs: Any,
    ) -> Future[Any]:
        if self._closed.is_set():
            raise CommandQueueClosed(f"command queue for {self.device_id} is closed")
        if self._cancel_requested.is_set():
            raise CommandCancelled(f"command queue for {self.device_id} is cancelled")

        with self._seq_lock:
            seq = self._next_seq
            self._next_seq += 1

        future: Future[Any] = Future()
        try:
            self._queue.put_nowait(_Command(seq, fn, args, kwargs, future))
        except queue.Full as exc:
            raise CommandQueueFull(
                f"command queue for {self.device_id} reached max backlog"
            ) from exc
        return future

    def _run(self) -> None:
        while True:
            command = self._queue.get()
            if command is None:
                self._queue.task_done()
                return
            try:
                if self._cancel_requested.is_set():
                    raise CommandCancelled(
                        f"command {command.seq} cancelled before execution"
                    )
                if command.seq <= self._last_completed_seq or self._active_execution:
                    self._order_violation_count += 1
                    raise CommandOrderViolation(
                        f"out-of-order or overlapping command seq={command.seq}"
                    )
                if not command.future.set_running_or_notify_cancel():
                    continue

                self._active_execution = True
                try:
                    result = command.fn(*command.args, **command.kwargs)
                finally:
                    self._active_execution = False

                self._last_completed_seq = command.seq
                command.future.set_result(result)
            except BaseException as exc:
                if not command.future.done():
                    command.future.set_exception(exc)
            finally:
                self._queue.task_done()

    def drain(self) -> None:
        self._queue.join()

    def close(self, *, cancel_pending: bool = True) -> None:
        if self._closed.is_set():
            return
        self._closed.set()
        if cancel_pending:
            self._cancel_requested.set()
        self._queue.put(None)
        self._worker.join(timeout=5.0)


class QueueBoundAdbWriter:
    """Only supported high-level path for ADB input writes."""

    def __init__(self, runner: AdbRunner, command_queue: DeviceCommandQueue) -> None:
        if runner.device_id != command_queue.device_id:
            raise ValueError("runner and command_queue must target the same device_id")
        self.runner = runner
        self.command_queue = command_queue

    @property
    def device_id(self) -> str:
        return self.runner.device_id

    def request_cancel(self) -> None:
        self.command_queue.request_cancel()

    def clear_cancel(self) -> None:
        self.command_queue.clear_cancel()

    def tap(self, x: int, y: int) -> Future[Any]:
        return self.command_queue.submit(
            self.runner.tap,
            x,
            y,
            _write_capability=_ADB_WRITE_CAPABILITY,
        )

    def swipe(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        duration_ms: int = 350,
    ) -> Future[Any]:
        return self.command_queue.submit(
            self.runner.swipe,
            x1,
            y1,
            x2,
            y2,
            duration_ms,
            _write_capability=_ADB_WRITE_CAPABILITY,
        )

    def back(self) -> Future[Any]:
        return self.command_queue.submit(
            self.runner.back,
            _write_capability=_ADB_WRITE_CAPABILITY,
        )

    def recover_adb_server(
        self,
        *,
        connect_target: str | None = None,
        wait_seconds: float = 10.0,
    ) -> Future[Any]:
        return self.command_queue.submit(
            self.runner.recover_adb_server,
            connect_target=connect_target,
            wait_seconds=wait_seconds,
        )
