from pathlib import Path

from simcity_ai_mayor.storage.session_store import SessionStore


def test_session_output_survives_store_reopen(tmp_path: Path) -> None:
    db = tmp_path / "state.db"

    first = SessionStore(db)
    session_a = first.get_or_create_active("mumu-0")
    first.record_output("mumu-0", "metal", 3)
    first.close()

    second = SessionStore(db)
    session_b = second.get_or_create_active("mumu-0")
    assert session_b.session_id == session_a.session_id
    assert second.item_output("mumu-0", "metal") == 3
    assert second.total_output("mumu-0") == 3
    second.close()


def test_reset_is_explicit_and_allows_multiple_closed_sessions(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state.db")
    first = store.get_or_create_active("mumu-0")
    store.record_output("mumu-0", "wood", 2)

    second = store.reset_session("mumu-0", reason="verified manual storage clear")
    assert second.session_id != first.session_id
    assert store.total_output("mumu-0") == 0

    third = store.reset_session("mumu-0", reason="new acceptance run")
    assert third.session_id != second.session_id
    store.close()


def test_reset_requires_reason(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "state.db")
    store.get_or_create_active("mumu-0")

    try:
        store.reset_session("mumu-0", reason="  ")
    except ValueError as exc:
        assert "reason" in str(exc)
    else:
        raise AssertionError("reset without reason must fail")
    finally:
        store.close()
