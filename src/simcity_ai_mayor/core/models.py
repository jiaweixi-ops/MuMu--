from __future__ import annotations

import math
from dataclasses import dataclass
from enum import IntEnum, StrEnum


class RunMode(StrEnum):
    DRY_RUN = "DRY_RUN"
    ASSIST = "ASSIST"
    AUTO = "AUTO"


class RiskLevel(IntEnum):
    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3


class ScreenType(StrEnum):
    CITY = "CITY"
    FACTORY = "FACTORY"
    SHOP = "SHOP"
    STORAGE = "STORAGE"
    LOADING = "LOADING"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN_POPUP = "UNKNOWN_POPUP"
    UNKNOWN = "UNKNOWN"


class FactoryState(StrEnum):
    IDLE = "IDLE"
    PRODUCING = "PRODUCING"
    COMPLETED_COLLECTABLE = "COMPLETED_COLLECTABLE"
    COMPLETED_STORAGE_BLOCKED = "COMPLETED_STORAGE_BLOCKED"
    COLLECTED = "COLLECTED"
    UNKNOWN = "UNKNOWN"


class AutomationState(StrEnum):
    BOOT = "BOOT"
    OBSERVE = "OBSERVE"
    PLAN = "PLAN"
    EXECUTE = "EXECUTE"
    VERIFY = "VERIFY"
    STORAGE_NORMAL = "STORAGE_NORMAL"
    STORAGE_WARNING = "STORAGE_WARNING"
    STORAGE_FULL = "STORAGE_FULL"
    WAIT_SESSION_CAP = "WAIT_SESSION_CAP"
    WAIT_STORAGE_RESERVE = "WAIT_STORAGE_RESERVE"
    WAIT_OCR_UNTRUSTED = "WAIT_OCR_UNTRUSTED"
    BLOCKED_STORAGE = "BLOCKED_STORAGE"
    RECOVER = "RECOVER"
    PAUSED = "PAUSED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class StorageCapacity:
    used: int
    capacity: int
    confidence: float = 0.0

    def __post_init__(self) -> None:
        if self.capacity <= 0:
            raise ValueError("capacity must be > 0")
        if self.used < 0 or self.used > self.capacity:
            raise ValueError("used must satisfy 0 <= used <= capacity")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def free(self) -> int:
        return self.capacity - self.used

    @property
    def ratio(self) -> float:
        return self.used / self.capacity


@dataclass(frozen=True, slots=True)
class V0Policy:
    collect_safety_margin: int = 2
    reserve_free_ratio: float = 0.10
    reserve_free_min: int = 10
    max_total_session_output: int = 12
    max_item_session_output: int = 8
    min_ocr_confidence: float = 0.99

    def __post_init__(self) -> None:
        if self.collect_safety_margin < 0:
            raise ValueError("collect_safety_margin must be >= 0")
        if not 0.0 <= self.reserve_free_ratio <= 1.0:
            raise ValueError("reserve_free_ratio must be between 0 and 1")
        if self.reserve_free_min < 0:
            raise ValueError("reserve_free_min must be >= 0")
        if self.max_total_session_output <= 0 or self.max_item_session_output <= 0:
            raise ValueError("session output caps must be positive")
        if not 0.0 <= self.min_ocr_confidence <= 1.0:
            raise ValueError("min_ocr_confidence must be between 0 and 1")

    def reserve_free(self, capacity: int) -> int:
        if capacity <= 0:
            raise ValueError("capacity must be > 0")
        ratio_reserve = math.ceil(capacity * self.reserve_free_ratio - 1e-9)
        return max(self.reserve_free_min, ratio_reserve)


@dataclass(frozen=True, slots=True)
class ProductionDecision:
    allowed: bool
    state: AutomationState
    reason: str


@dataclass(frozen=True, slots=True)
class CollectDecision:
    allowed: bool
    reason: str
