import pytest

from simcity_ai_mayor.core.models import RiskLevel, RunMode, ScreenType
from simcity_ai_mayor.executor.keeper import (
    ACTIONABLE_SCREENS,
    ActionRequest,
    Decision,
    Keeper,
    RateWindow,
    ReasonCode,
)


@pytest.mark.parametrize("screen", list(ScreenType))
def test_keeper_screen_policy_is_fail_closed(screen: ScreenType) -> None:
    keeper = Keeper(device_id="mumu-0", run_mode=RunMode.AUTO)
    request = ActionRequest(
        device_id="mumu-0",
        task_id="task-1",
        name="tap",
        risk=RiskLevel.L0,
        screen=screen,
    )
    result = keeper.evaluate(request, recent=RateWindow())
    assert result.allowed is (screen in ACTIONABLE_SCREENS)
    if screen not in ACTIONABLE_SCREENS:
        assert result.reason_code is ReasonCode.SCREEN_NOT_ACTIONABLE


def test_assist_requires_explicit_human_approval() -> None:
    keeper = Keeper(device_id="mumu-0", run_mode=RunMode.ASSIST)
    request = ActionRequest(
        device_id="mumu-0",
        task_id="task-1",
        name="collect",
        risk=RiskLevel.L1,
        screen=ScreenType.FACTORY,
    )
    pending = keeper.evaluate(request, recent=RateWindow())
    assert pending.decision is Decision.NEEDS_HUMAN

    approved = keeper.evaluate(
        ActionRequest(
            device_id=request.device_id,
            task_id=request.task_id,
            name=request.name,
            risk=request.risk,
            screen=request.screen,
            human_approved=True,
        ),
        recent=RateWindow(),
    )
    assert approved.decision is Decision.ALLOW


def test_auto_l3_still_requires_human_then_allows_approved_replay() -> None:
    keeper = Keeper(device_id="mumu-0", run_mode=RunMode.AUTO)
    request = ActionRequest(
        device_id="mumu-0",
        task_id="task-l3",
        name="sell",
        risk=RiskLevel.L3,
        screen=ScreenType.STORAGE,
    )

    pending = keeper.evaluate(request, recent=RateWindow())
    assert pending.decision is Decision.NEEDS_HUMAN
    assert pending.reason_code is ReasonCode.RISK_REQUIRES_HUMAN

    approved = keeper.evaluate(
        ActionRequest(
            device_id=request.device_id,
            task_id=request.task_id,
            name=request.name,
            risk=request.risk,
            screen=request.screen,
            human_approved=True,
        ),
        recent=RateWindow(),
    )
    assert approved.decision is Decision.ALLOW
    assert approved.reason_code is ReasonCode.APPROVED


def test_keeper_rejects_device_mismatch() -> None:
    keeper = Keeper(device_id="mumu-0", run_mode=RunMode.AUTO)
    result = keeper.evaluate(
        ActionRequest(
            device_id="mumu-1",
            task_id="task-1",
            name="tap",
            risk=RiskLevel.L0,
            screen=ScreenType.CITY,
        ),
        recent=RateWindow(),
    )
    assert result.decision is Decision.DENY
    assert result.reason_code is ReasonCode.DEVICE_MISMATCH
