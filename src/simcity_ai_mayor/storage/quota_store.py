from __future__ import annotations

from simcity_ai_mayor.storage.session_store import SessionStore


class QuotaSessionStore(SessionStore):
    """SessionStore with conservative output reservation rollback.

    Production reserves quota before the device write. A reservation is released only
    when a trustworthy fresh-state verifier explicitly proves the production did not
    start. Ambiguous outcomes intentionally remain charged.
    """

    def release_output(self, device_id: str, item: str, count: int = 1) -> None:
        self._validate_device_id(device_id)
        if not item.strip():
            raise ValueError("item is required")
        if count <= 0:
            raise ValueError("count must be positive")
        with self._lock, self._conn:
            session = self._get_or_create_active_locked(device_id)
            row = self._conn.execute(
                "SELECT output_count FROM v0_session_output "
                "WHERE session_id=? AND item=?",
                (session["session_id"], item),
            ).fetchone()
            current = int(row[0]) if row else 0
            if current < count:
                raise ValueError(
                    f"cannot release {count} output for {item!r}; only {current} reserved"
                )
            remaining = current - count
            if remaining == 0:
                self._conn.execute(
                    "DELETE FROM v0_session_output WHERE session_id=? AND item=?",
                    (session["session_id"], item),
                )
            else:
                self._conn.execute(
                    "UPDATE v0_session_output SET output_count=? "
                    "WHERE session_id=? AND item=?",
                    (remaining, session["session_id"], item),
                )
