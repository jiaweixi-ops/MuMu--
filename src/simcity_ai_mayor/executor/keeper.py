from __future__ import annotations

from dataclasses import dataclass

from simcity_ai_mayor.core.models import RiskLevel, RunMode, ScreenType


@dataclass(frozen=True, slots=True)
class ActionRequest:
    name: str
    risk: RiskLevel
    screen: ScreenType
    requires_premium_currency: bool = False


@dataclass(frozen=True, slots=True)
class KeeperDecision:
    allowed: bool
    reason: str


class Keeper:
    """Final policy gate before an action reaches the ADB command queue."""

    def __init__(
        self,
        *,
        run_mode: RunMode = RunMode.DRY_RUN,
        max_auto_risk: RiskLevel = RiskLevel.L2,
        allow_premium_currency: bool = False,
    ) -> None:
        self.run_mode = run_mode
        self.max_auto_risk = max_auto_risk
        self.allow_premium_currency = allow_premium_currency

    def evaluate(self, request: ActionRequest) -> KeeperDecision:
        if self.run_mode in {RunMode.DRY_RUN, RunMode.ASSIST}:
            return KeeperDecision(False, f"run mode {self.run_mode.value} does not execute actions")

        if request.screen in {ScreenType.UNKNOWN, ScreenType.LOADING}:
            return KeeperDecision(False, f"unsafe screen: {request.screen.value}")

        if request.requires_premium_currency and not self.allow_premium_currency:
            return KeeperDecision(False, "premium-currency spending is disabled")

        if request.risk > self.max_auto_risk:
            return KeeperDecision(
                False,
                f"risk {request.risk.name} exceeds auto limit {self.max_auto_risk.name}",
            )

        return KeeperDecision(True, "keeper approved")
