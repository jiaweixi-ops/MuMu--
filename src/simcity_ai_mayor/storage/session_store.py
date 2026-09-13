from __future__ import annotations

import sqlite3
import threading
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path


@dataclass(frozen=True, slots=True)
class V0Session:
    session_id: str
    device_id: str
    started_at: str
    status: str
    total_output: int


class SessionStore:
    """Persistent V0 session accounting.

    A session survives process/game/ADB/Windows restarts. It only ends through an
    explicit reset operation with a reason, which prevents restart-based cap bypass.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
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

                CREATE INDEX IF NOT EXISTS idx_v0_sessions_device
                ON v0_sessions(device_id, status);
                """
            )

    def get_or_create_active(self, device_id: str) -> V0Session:
        if not device_id.strip():
            raise ValueError("device_id is required")
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT * FROM v0_sessions WHERE device_id=? AND status='ACTIVE'",
                (device_id,),
            ).fetchone()
            if row is None:
                session_id = str(uuid.uuid4())
                started_at = datetime.now(UTC).isoformat()
                self._conn.execute(
                    "INSERT INTO v0_sessions(session_id, device_id, started_at, status) "
                    "VALUES(?, ?, ?, 'ACTIVE')",
                    (session_id, device_id, started_at),
                )
                row = self._conn.execute(
                    "SELECT * FROM v0_sessions WHERE session_id=?", (session_id,)
                ).fetchone()
            return self._to_session(row)

    def record_output(self, device_id: str, item: str, count: int = 1) -> None:
        if not item.strip():
            raise ValueError("item is required")
        if count <= 0:
            raise ValueError("count must be positive")
        with self._lock, self._conn:
            session = self.get_or_create_active(device_id)
            self._conn.execute(
                """
                INSERT INTO v0_session_output(session_id, item, output_count)
                VALUES(?, ?, ?)
                ON CONFLICT(session_id, item)
                DO UPDATE SET output_count=output_count+excluded.output_count
                """,
                (session.session_id, item, count),
            )

    def item_output(self, device_id: str, item: str) -> int:
        with self._lock:
            session = self.get_or_create_active(device_id)
            row = self._conn.execute(
                "SELECT output_count FROM v0_session_output WHERE session_id=? AND item=?",
                (session.session_id, item),
            ).fetchone()
            return int(row[0]) if row else 0

    def total_output(self, device_id: str) -> int:
        with self._lock:
            session = self.get_or_create_active(device_id)
            row = self._conn.execute(
                "SELECT COALESCE(SUM(output_count), 0) FROM v0_session_output WHERE session_id=?",
                (session.session_id,),
            ).fetchone()
            return int(row[0])

    def reset_session(self, device_id: str, *, reason: str) -> V0Session:
        """Explicitly close the active session and create a fresh one.

        Normal restarts must not call this method. Valid reasons are human actions such
        as a verified storage-clear event or an explicit acceptance-test reset.
        """

        if not reason.strip():
            raise ValueError("explicit reset reason is required")
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            active = self._conn.execute(
                "SELECT session_id FROM v0_sessions WHERE device_id=? AND status='ACTIVE'",
                (device_id,),
            ).fetchone()
            if active:
                self._conn.execute(
                    "UPDATE v0_sessions SET status='CLOSED', ended_at=?, reset_reason=? "
                    "WHERE session_id=?",
                    (now, reason, active[0]),
                )
            return self.get_or_create_active(device_id)

    def _to_session(self, row: sqlite3.Row) -> V0Session:
        total = self._conn.execute(
            "SELECT COALESCE(SUM(output_count), 0) FROM v0_session_output WHERE session_id=?",
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
