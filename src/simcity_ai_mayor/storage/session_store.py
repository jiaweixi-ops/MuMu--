from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from simcity_ai_mayor.core.acceptance import V0AcceptanceTracker


@dataclass(frozen=True, slots=True)
class V0Session:
    session_id: str
    device_id: str
    started_at: str
    status: str
    total_output: int


class SessionStore:
    """Single-connection SQLite store for V0 session and acceptance state."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(
            self.path,
            check_same_thread=False,
            timeout=5.0,
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._init_schema()

    def _init_schema(self) -> None:
        with self._conn:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS v0_sessions (
                    session_id TEXT PRIMARY KEY,
                    device_id TEXT NOT NULL,
                    started_at TEXT NOT NULL,
                    ended_at TEXT,
                    reset_reason TEXT,
                    status TEXT NOT NULL CHECK(status IN ('ACTIVE', 'CLOSED'))
                );

                CREATE UNIQUE INDEX IF NOT EXISTS ux_v0_one_active_session_per_device
                ON v0_sessions(device_id)
                WHERE status='ACTIVE';

                CREATE TABLE IF NOT EXISTS v0_session_output (
                    session_id TEXT NOT NULL,
                    item TEXT NOT NULL,
                    output_count INTEGER NOT NULL DEFAULT 0 CHECK(output_count >= 0),
                    PRIMARY KEY(session_id, item),
                    FOREIGN KEY(session_id) REFERENCES v0_sessions(session_id)
                );

                CREATE TABLE IF NOT EXISTS v0_acceptance (
                    device_id TEXT PRIMARY KEY,
                    collect_success INTEGER NOT NULL DEFAULT 0,
                    produce_success INTEGER NOT NULL DEFAULT 0,
                    unsafe_purchase INTEGER NOT NULL DEFAULT 0,
                    unsafe_sale INTEGER NOT NULL DEFAULT 0,
                    premium_spend INTEGER NOT NULL DEFAULT 0,
                    infinite_loop_incidents INTEGER NOT NULL DEFAULT 0,
                    stale_state_actions INTEGER NOT NULL DEFAULT 0,
                    adb_write_order_incidents INTEGER NOT NULL DEFAULT 0,
                    effective_runtime_seconds REAL NOT NULL DEFAULT 0,
                    wait_session_cap_seconds REAL NOT NULL DEFAULT 0,
                    blocked_storage_seconds REAL NOT NULL DEFAULT 0,
                    blocked_storage_detected INTEGER NOT NULL DEFAULT 0,
                    manual_clear_recovered INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS v0_acceptance_transitions (
                    device_id TEXT NOT NULL,
                    transition TEXT NOT NULL,
                    transition_count INTEGER NOT NULL DEFAULT 0,
                    PRIMARY KEY(device_id, transition)
                );

                CREATE INDEX IF NOT EXISTS idx_v0_sessions_device
                ON v0_sessions(device_id, status);
                """
            )

    def _validate_device_id(self, device_id: str) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")

    def _get_active_locked(self, device_id: str) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM v0_sessions WHERE device_id=? AND status='ACTIVE'",
            (device_id,),
        ).fetchone()

    def _create_active_locked(self, device_id: str) -> sqlite3.Row:
        session_id = str(uuid.uuid4())
        started_at = datetime.now(UTC).isoformat()
        self._conn.execute(
            "INSERT INTO v0_sessions(session_id, device_id, started_at, status) "
            "VALUES(?, ?, ?, 'ACTIVE')",
            (session_id, device_id, started_at),
        )
        row = self._conn.execute(
            "SELECT * FROM v0_sessions WHERE session_id=?",
            (session_id,),
        ).fetchone()
        assert row is not None
        return row

    def _get_or_create_active_locked(self, device_id: str) -> sqlite3.Row:
        row = self._get_active_locked(device_id)
        return row if row is not None else self._create_active_locked(device_id)

    def get_or_create_active(self, device_id: str) -> V0Session:
        self._validate_device_id(device_id)
        with self._lock, self._conn:
            row = self._get_or_create_active_locked(device_id)
            return self._to_session(row)

    def record_output(self, device_id: str, item: str, count: int = 1) -> None:
        self._validate_device_id(device_id)
        if not item.strip():
            raise ValueError("item is required")
        if count <= 0:
            raise ValueError("count must be positive")
        with self._lock, self._conn:
            session = self._get_or_create_active_locked(device_id)
            self._conn.execute(
                """
                INSERT INTO v0_session_output(session_id, item, output_count)
                VALUES(?, ?, ?)
                ON CONFLICT(session_id, item)
                DO UPDATE SET output_count=output_count+excluded.output_count
                """,
                (session["session_id"], item, count),
            )

    def item_output(self, device_id: str, item: str) -> int:
        self._validate_device_id(device_id)
        with self._lock, self._conn:
            session = self._get_or_create_active_locked(device_id)
            row = self._conn.execute(
                "SELECT output_count FROM v0_session_output WHERE session_id=? AND item=?",
                (session["session_id"], item),
            ).fetchone()
            return int(row[0]) if row else 0

    def total_output(self, device_id: str) -> int:
        self._validate_device_id(device_id)
        with self._lock, self._conn:
            session = self._get_or_create_active_locked(device_id)
            row = self._conn.execute(
                "SELECT COALESCE(SUM(output_count), 0) "
                "FROM v0_session_output WHERE session_id=?",
                (session["session_id"],),
            ).fetchone()
            return int(row[0])

    def reset_session(self, device_id: str, *, reason: str) -> V0Session:
        """Atomically close the active session and create a fresh one."""
        self._validate_device_id(device_id)
        if not reason.strip():
            raise ValueError("explicit reset reason is required")
        now = datetime.now(UTC).isoformat()

        with self._lock, self._conn:
            active = self._get_active_locked(device_id)
            if active:
                self._conn.execute(
                    "UPDATE v0_sessions SET status='CLOSED', ended_at=?, reset_reason=? "
                    "WHERE session_id=?",
                    (now, reason, active["session_id"]),
                )
            new_row = self._create_active_locked(device_id)
            return self._to_session(new_row)

    def save_acceptance(self, device_id: str, tracker: V0AcceptanceTracker) -> None:
        """Persist acceptance coverage so process restarts do not erase a run."""
        self._validate_device_id(device_id)
        updated_at = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO v0_acceptance(
                    device_id, collect_success, produce_success, unsafe_purchase,
                    unsafe_sale, premium_spend, infinite_loop_incidents,
                    stale_state_actions, adb_write_order_incidents,
                    effective_runtime_seconds, wait_session_cap_seconds,
                    blocked_storage_seconds, blocked_storage_detected,
                    manual_clear_recovered, updated_at
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(device_id) DO UPDATE SET
                    collect_success=excluded.collect_success,
                    produce_success=excluded.produce_success,
                    unsafe_purchase=excluded.unsafe_purchase,
                    unsafe_sale=excluded.unsafe_sale,
                    premium_spend=excluded.premium_spend,
                    infinite_loop_incidents=excluded.infinite_loop_incidents,
                    stale_state_actions=excluded.stale_state_actions,
                    adb_write_order_incidents=excluded.adb_write_order_incidents,
                    effective_runtime_seconds=excluded.effective_runtime_seconds,
                    wait_session_cap_seconds=excluded.wait_session_cap_seconds,
                    blocked_storage_seconds=excluded.blocked_storage_seconds,
                    blocked_storage_detected=excluded.blocked_storage_detected,
                    manual_clear_recovered=excluded.manual_clear_recovered,
                    updated_at=excluded.updated_at
                """,
                (
                    device_id,
                    tracker.collect_success,
                    tracker.produce_success,
                    tracker.unsafe_purchase,
                    tracker.unsafe_sale,
                    tracker.premium_spend,
                    tracker.infinite_loop_incidents,
                    tracker.stale_state_actions,
                    tracker.adb_write_order_incidents,
                    tracker.effective_runtime_seconds,
                    tracker.wait_session_cap_seconds,
                    tracker.blocked_storage_seconds,
                    int(tracker.blocked_storage_detected),
                    int(tracker.manual_clear_recovered),
                    updated_at,
                ),
            )
            self._conn.execute(
                "DELETE FROM v0_acceptance_transitions WHERE device_id=?",
                (device_id,),
            )
            self._conn.executemany(
                """
                INSERT INTO v0_acceptance_transitions(
                    device_id, transition, transition_count
                ) VALUES(?, ?, ?)
                """,
                [
                    (device_id, transition, count)
                    for transition, count in tracker.transitions.items()
                ],
            )

    def load_acceptance(self, device_id: str) -> V0AcceptanceTracker:
        self._validate_device_id(device_id)
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM v0_acceptance WHERE device_id=?",
                (device_id,),
            ).fetchone()
            if row is None:
                return V0AcceptanceTracker()

            transitions = {
                transition_row["transition"]: int(transition_row["transition_count"])
                for transition_row in self._conn.execute(
                    "SELECT transition, transition_count "
                    "FROM v0_acceptance_transitions WHERE device_id=?",
                    (device_id,),
                ).fetchall()
            }
            return V0AcceptanceTracker(
                collect_success=int(row["collect_success"]),
                produce_success=int(row["produce_success"]),
                unsafe_purchase=int(row["unsafe_purchase"]),
                unsafe_sale=int(row["unsafe_sale"]),
                premium_spend=int(row["premium_spend"]),
                infinite_loop_incidents=int(row["infinite_loop_incidents"]),
                stale_state_actions=int(row["stale_state_actions"]),
                adb_write_order_incidents=int(row["adb_write_order_incidents"]),
                effective_runtime_seconds=float(row["effective_runtime_seconds"]),
                wait_session_cap_seconds=float(row["wait_session_cap_seconds"]),
                blocked_storage_seconds=float(row["blocked_storage_seconds"]),
                blocked_storage_detected=bool(row["blocked_storage_detected"]),
                manual_clear_recovered=bool(row["manual_clear_recovered"]),
                transitions=transitions,
            )

    def reset_acceptance(self, device_id: str) -> None:
        self._validate_device_id(device_id)
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM v0_acceptance_transitions WHERE device_id=?",
                (device_id,),
            )
            self._conn.execute(
                "DELETE FROM v0_acceptance WHERE device_id=?",
                (device_id,),
            )

    def _to_session(self, row: sqlite3.Row) -> V0Session:
        total = self._conn.execute(
            "SELECT COALESCE(SUM(output_count), 0) "
            "FROM v0_session_output WHERE session_id=?",
            (row["session_id"],),
        ).fetchone()[0]
        return V0Session(
            session_id=row["session_id"],
            device_id=row["device_id"],
            started_at=row["started_at"],
            status=row["status"],
            total_output=int(total),
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()
