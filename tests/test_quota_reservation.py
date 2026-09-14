from simcity_ai_mayor.core.models import (
    AutomationState,
    FactoryState,
    ScreenType,
    StorageCapacity,
)
from simcity_ai_mayor.planner.v0 import ProductionRecipe, TapPoint, V0Planner
from simcity_ai_mayor.runtime.orchestrator import Observation, PlannedAction
from simcity_ai_mayor.storage.quota_store import QuotaSessionStore
from simcity_ai_mayor.verifier.predicates import Verdict


class Writer:
    def tap(self, x, y):
        del x, y
        return object()


def idle_observation() -> Observation:
    return Observation(
        device_id="mumu-0",
        frame_id=1,
        captured_at=1.0,
        screen=ScreenType.FACTORY,
        automation_state=AutomationState.OBSERVE,
        storage=StorageCapacity(10, 100, 1.0),
        factory_state=FactoryState.IDLE,
    )


def test_production_quota_rolls_back_only_on_proven_failure(tmp_path) -> None:
    store = QuotaSessionStore(tmp_path / "quota.db")
    planner = V0Planner(
        device_id="mumu-0",
        session_store=store,
        production=ProductionRecipe("metal", TapPoint(10, 20)),
    )
    action = planner.plan(idle_observation())
    assert isinstance(action, PlannedAction)

    action.execute(Writer())
    assert store.item_output("mumu-0", "metal") == 1

    result = action.verify(
        {"factory_state": FactoryState.IDLE.value},
        {"factory_state": FactoryState.IDLE.value},
    )

    assert result.verdict is Verdict.FAIL
    assert store.item_output("mumu-0", "metal") == 0
    store.close()


def test_unknown_production_verification_keeps_conservative_reservation(tmp_path) -> None:
    store = QuotaSessionStore(tmp_path / "quota.db")
    planner = V0Planner(
        device_id="mumu-0",
        session_store=store,
        production=ProductionRecipe("metal", TapPoint(10, 20)),
    )
    action = planner.plan(idle_observation())
    assert isinstance(action, PlannedAction)

    action.execute(Writer())
    result = action.verify(
        {"factory_state": FactoryState.IDLE.value},
        {},
    )

    assert result.verdict is Verdict.UNKNOWN
    assert store.item_output("mumu-0", "metal") == 1
    store.close()


def test_quota_store_refuses_over_release(tmp_path) -> None:
    store = QuotaSessionStore(tmp_path / "quota.db")
    store.record_output("mumu-0", "metal", 1)

    try:
        store.release_output("mumu-0", "metal", 2)
    except ValueError as exc:
        assert "only 1 reserved" in str(exc)
    else:
        raise AssertionError("over-release must fail closed")
    assert store.item_output("mumu-0", "metal") == 1
    store.close()
