from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from simcity_ai_mayor.core.models import RiskLevel, RunMode, ScreenType

ACTIONABLE_SCREENS: Final[frozenset[ScreenType]] = frozenset(
    {
        ScreenType.CITY,
        ScreenType.FACTORY,
        ScreenType.SHOP,
        ScreenType.STORAGE,
    }
)


class Decision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    NEEDS_HUMAN = "NEEDS_HUMAN"


class ReasonCode(StrEnum):
    APPROVED = "APPROVED"
    DRY_RUN = "DRY_RUN"
    HUMAN_APPROVAL_REQUIRED = "HUMAN_APPROVAL_REQUIRED"
    DEVICE_MISMATCH = "DEVICE_MISMATCH"
    SCREEN_NOT_ACTIONABLE = "SCREEN_NOT_ACTIONABLE"
    PREMIUM_CURRENCY_DISABLED = "PREMIUM_CURRENCY_DISABLED"
    RISK_REQUIRES_HUMAN = "RISK_REQUIRES_HUMAN"
    RATE_LIMIT = "RATE_LIMIT"
    RETRY_LIMIT = "RETRY_LIMIT"
    ACTION_COOLDOWN = "ACTION_COOLDOWN"
    CIRCUIT_BREAKER = "CIRCUIT_BREAKER"


@dataclass(frozen=True, slots=True)
class ActionRequest:
    device_id: str
    task_id: str
    name: str
    risk: RiskLevel
    screen: ScreenType
    requires_premium_currency: bool = False
    human_approved: bool = False


@dataclass(frozen=True, slots=True)
class RateWindow:
    actions_last_minute: int = 0
    retries_for_action: int = 0
    same_action_cooling_down: bool = False
    samples_5m: int = 0
    failures_5m: int = 0

    @property
    def failure_rate_5m(self) -> float:
        if self.samples_5m <= 0:
            return 0.0
        return self.failures_5m / self.samples_5m


@dataclass(frozen=True, slots=True)
class KeeperDecision:
    decision: Decision
    reason_code: ReasonCode
    reason: str

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


class Keeper:
    """Final fail-closed policy gate before an action reaches the ADB queue."""

    def __init__(
        self,
        *,
        device_id: str,
        run_mode: RunMode = RunMode.DRY_RUN,
        max_auto_risk: RiskLevel = RiskLevel.L2,
        allow_premium_currency: bool = False,
        max_actions_per_minute: int = 15,
        max_retries_per_action: int = 3,
        circuit_breaker_min_samples: int = 10,
        circuit_breaker_failure_rate: float = 0.30,
    ) -> None:
        if not device_id.strip():
            raise ValueError("device_id is required")
        self.device_id = device_id
        self.run_mode = run_mode
        self.max_auto_risk = max_auto_risk
        self.allow_premium_currency = allow_premium_currency
        self.max_actions_per_minute = max_actions_per_minute
        self.max_retries_per_action = max_retries_per_action
        self.circuit_breaker_min_samples = circuit_breaker_min_samples
        self.circuit_breaker_failure_rate = circuit_breaker_failure_rate

    def evaluate(
        self,
        request: ActionRequest,
        *,
        now: float,
        recent: RateWindow,
    ) -> KeeperDecision:
        del now

        if request.device_id != self.device_id:
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.DEVICE_MISMATCH,
                f"request device {request.device_id!r} != keeper device {self.device_id!r}",
            )

        if self.run_mode is RunMode.DRY_RUN:
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.DRY_RUN,
                "DRY_RUN never sends business ADB actions",
            )

        if request.screen not in ACTIONABLE_SCREENS:
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.SCREEN_NOT_ACTIONABLE,
                f"screen not actionable: {request.screen.value}",
            )

        if request.requires_premium_currency and not self.allow_premium_currency:
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.PREMIUM_CURRENCY_DISABLED,
                "premium-currency spending is disabled",
            )

        if recent.actions_last_minute >= self.max_actions_per_minute:
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.RATE_LIMIT,
                "action rate limit reached",
            )
        if recent.retries_for_action >= self.max_retries_per_action:
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.RETRY_LIMIT,
                "retry limit reached",
            )
        if recent.same_action_cooling_down:
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.ACTION_COOLDOWN,
                "same action is cooling down",
            )
        if (
            recent.samples_5m >= self.circuit_breaker_min_samples
            and recent.failure_rate_5m > self.circuit_breaker_failure_rate
        ):
            return KeeperDecision(
                Decision.DENY,
                ReasonCode.CIRCUIT_BREAKER,
                f"5m failure rate {recent.failure_rate_5m:.1%} exceeds threshold",
            )

        needs_human = self.run_mode is RunMode.ASSIST or request.risk > self.max_auto_risk
        if needs_human and not request.human_approved:
            code = (
                ReasonCode.RISK_REQUIRES_HUMAN
                if request.risk > self.max_auto_risk
                else ReasonCode.HUMAN_APPROVAL_REQUIRED
            )
            return KeeperDecision(
                Decision.NEEDS_HUMAN,
                code,
                "human approval is required before this action may execute",
            )

        return KeeperDecision(
            Decision.ALLOW,
            ReasonCode.APPROVED,
            "keeper approved",
        )
