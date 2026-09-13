from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, IntEnum


class RunMode(str, Enum):
    DRY_RUN = "DRY_RUN"
    ASSIST = "ASSIST"
    AUTO = "AUTO"


class RiskLevel(IntEnum):
    L0 = 0
    L1 = 1
    L2 = 2
    L3 = 3


class ScreenType(str, Enum):
    CITY = "CITY"
    FACTORY = "FACTORY"
    SHOP = "SHOP"
    STORAGE = "STORAGE"
    LOADING = "LOADING"
    NETWORK_ERROR = "NETWORK_ERROR"
    UNKNOWN = "UNKNOWN"


class FactoryState(str, Enum):
    IDLE = "IDLE"
    PRODUCING = "PRODUCING"
    COMPLETED_COLLECTABLE = "COMPLETED_COLLECTABLE"
    COMPLETED_STORAGE_BLOCKED = "COMPLETED_STORAGE_BLOCKED"
    UNKNOWN = "UNKNOWN"


class AutomationState(str, Enum):
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
    BLOCKED_STORAGE = "BLOCKED_STORAGE"
    RECOVER = "RECOVER"
    PAUSED = "PAUSED"
    EMERGENCY_STOP = "EMERGENCY_STOP"
    STOPPED = "STOPPED"


@dataclass(frozen=True, slots=True)
class StorageCapacity:
    used: int
    capacity: int
    confidence: float = 1.0

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

    def reserve_free(self, capacity: int) -> int:
        ratio_reserve = int(capacity * self.reserve_free_ratio + 0.999999)
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
