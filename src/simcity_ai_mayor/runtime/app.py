from __future__ import annotations

import argparse
import sys
from pathlib import Path

from simcity_ai_mayor.core.models import RunMode
from simcity_ai_mayor.device.adb import AdbError, AdbRunner
from simcity_ai_mayor.device.command_queue import DeviceCommandQueue, QueueBoundAdbWriter
from simcity_ai_mayor.executor.keeper import Keeper
from simcity_ai_mayor.planner.v0 import V0Planner
from simcity_ai_mayor.runtime.config import AppConfig, RuntimeConfigError
from simcity_ai_mayor.runtime.emergency_stop import EmergencyStop
from simcity_ai_mayor.runtime.metrics import RuntimeMetrics
from simcity_ai_mayor.runtime.orchestrator import CycleStatus, V0Orchestrator
from simcity_ai_mayor.storage.session_store import MetricsLeaseConflict, SessionStore
from simcity_ai_mayor.vision.observer import (
    AdbScreenshotObserver,
    ConservativeAutomationStateResolver,
    NullStorageReader,
    UnknownFactoryReader,
    UnknownScreenClassifier,
)
from simcity_ai_mayor.vision.readers import (
    FixedAnchorFactoryReader,
    FixedAnchorScreenClassifier,
    RapidOcrBackend,
    StorageRoiReader,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the SimCity V0 trusted loop")
    parser.add_argument("--config", required=True, help="Path to V0 JSON config")
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=None,
        help="Override configured bounded runtime",
    )
    return parser


def run_app(config: AppConfig, *, duration_seconds: float | None = None) -> int:
    duration = config.duration_seconds if duration_seconds is None else duration_seconds
    if duration <= 0:
        raise ValueError("duration_seconds must be > 0")

    store = SessionStore(config.state_db)
    metrics: RuntimeMetrics | None = None
    command_queue: DeviceCommandQueue | None = None
    emergency_stop: EmergencyStop | None = None
    try:
        runner = AdbRunner(
            config.device_id,
            adb_path=config.adb_path,
        )
        _validate_device_and_game(runner, config)

        command_queue = DeviceCommandQueue(config.device_id)
        writer = QueueBoundAdbWriter(runner, command_queue)
        metrics = RuntimeMetrics(device_id=config.device_id, store=store)

        screen_classifier = (
            FixedAnchorScreenClassifier(
                config.vision.screen_rules,
                ambiguity_margin=config.vision.screen_ambiguity_margin,
            )
            if config.vision.screen_rules
            else UnknownScreenClassifier()
        )
        factory_reader = (
            FixedAnchorFactoryReader(
                config.vision.factory_rules,
                ambiguity_margin=config.vision.factory_ambiguity_margin,
            )
            if config.vision.factory_rules
            else UnknownFactoryReader()
        )
        storage_reader = (
            StorageRoiReader(
                roi=config.vision.storage_roi,
                backend=RapidOcrBackend(),
                enabled_screens=config.vision.storage_screens,
            )
            if config.vision.storage_roi is not None
            else NullStorageReader()
        )
        observer = AdbScreenshotObserver(
            runner=runner,
            screen_classifier=screen_classifier,
            storage_reader=storage_reader,
            factory_reader=factory_reader,
            state_resolver=ConservativeAutomationStateResolver(
                config.policy,
                storage_required_screens=config.vision.storage_screens,
            ),
            expected_size=config.expected_size,
        )
        planner = V0Planner(
            device_id=config.device_id,
            session_store=store,
            policy=config.policy,
            collect_tap=config.collect_tap,
            production=config.production,
        )
        keeper = Keeper(
            device_id=config.device_id,
            run_mode=config.run_mode,
            allow_premium_currency=False,
        )
        emergency_stop = EmergencyStop(data_root=config.data_root)
        channels = emergency_stop.start_watchers()
        print(
            "Emergency stop channels: "
            f"flag={channels.stop_flag}, hotkey={channels.hotkey}, "
            f"hotkey_reason={channels.hotkey_reason!r}"
        )

        orchestrator = V0Orchestrator(
            device_id=config.device_id,
            observer=observer,
            planner=planner,
            keeper=keeper,
            writer=writer,
            metrics=metrics,
            emergency_stop=emergency_stop,
            action_timeout_seconds=config.action_timeout_seconds,
            v0_policy=config.policy,
        )
        result = orchestrator.run_for(
            duration,
            idle_sleep_seconds=config.idle_sleep_seconds,
        )
        if result is None:
            print("V0 run completed without executing a cycle")
            return 0
        print(f"V0 run finished: status={result.status.value}, error={result.error!r}")
        if result.status is CycleStatus.EMERGENCY_STOP:
            return 130
        if result.status is CycleStatus.OBSERVE_FAILED and result.error:
            return 2
        return 0
    finally:
        if emergency_stop is not None:
            emergency_stop.close()
        if command_queue is not None:
            command_queue.close(cancel_pending=True)
        if metrics is not None:
            metrics.close()
        store.close()


def _validate_device_and_game(runner: AdbRunner, config: AppConfig) -> None:
    try:
        runner.wait_for_device(10.0)
    except AdbError:
        runner.recover_adb_server(wait_seconds=10.0)

    game_version = runner.package_version(config.package_name)
    if game_version is None:
        raise AdbError(f"package {config.package_name!r} has no readable versionName")
    if (
        config.expected_game_version is not None
        and game_version != config.expected_game_version
    ):
        raise AdbError(
            f"game version {game_version!r} != frozen baseline "
            f"{config.expected_game_version!r}"
        )
    print(
        f"Device ready: {config.device_id}; package={config.package_name}; "
        f"version={game_version}; mode={config.run_mode.value}"
    )
    if config.run_mode is RunMode.AUTO:
        print("AUTO mode enabled; Keeper hard gates remain active")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = AppConfig.load(Path(args.config))
        return run_app(config, duration_seconds=args.duration_seconds)
    except (RuntimeConfigError, MetricsLeaseConflict, AdbError, ValueError) as exc:
        print(f"startup failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
