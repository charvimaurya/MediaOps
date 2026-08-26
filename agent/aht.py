"""
AHT (Automated Handling Time) timer and escalation-path policy.

AHT starts at incident creation (incident.created_at), not at whatever
moment the agent happens to pick the incident up -- start() takes that
reference timestamp explicitly rather than defaulting to "now", so any
delay between detection and the agent starting work still counts.
"""

import os
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class AHTTimer:
    def __init__(self):
        self._start: Optional[datetime] = None
        self._stopped_elapsed: Optional[float] = None

    def start(self, created_at: Optional[datetime] = None) -> None:
        self._start = created_at or datetime.now(timezone.utc)
        self._stopped_elapsed = None

    def elapsed(self) -> float:
        if self._start is None:
            return 0.0
        if self._stopped_elapsed is not None:
            return self._stopped_elapsed
        return (datetime.now(timezone.utc) - self._start).total_seconds()

    def stop(self) -> float:
        self._stopped_elapsed = self.elapsed()
        return self._stopped_elapsed


class EscalationPath(str, Enum):
    NORMAL = "NORMAL"
    FAST = "FAST"
    FAILSAFE = "FAILSAFE"


# Demo values, not production standards -- small enough that the
# escalation path can actually be reached within a sub-five-second
# incident. Loadable from the environment (AHT_FAST_ELAPSED_SECONDS /
# AHT_FAILSAFE_ELAPSED_SECONDS) so thresholds can be retuned during demo
# rehearsal without editing code. Read once at import time, matching how
# every other CONFIG-style constant in this project behaves -- restart
# the process to pick up a changed env var.
CONFIG = {
    "fast_elapsed_seconds": float(os.environ.get("AHT_FAST_ELAPSED_SECONDS", 1.5)),
    "failsafe_elapsed_seconds": float(os.environ.get("AHT_FAILSAFE_ELAPSED_SECONDS", 3.5)),
}

_PATH_ORDER = {EscalationPath.NORMAL: 0, EscalationPath.FAST: 1, EscalationPath.FAILSAFE: 2}


class EscalationPolicy:
    """Decides the current path from two independent inputs; the
    effective path is whichever is more severe."""

    def decide(self, attempts_failed: int, elapsed_seconds: float) -> EscalationPath:
        by_attempts = self._path_from_attempts(attempts_failed)
        by_time = self._path_from_elapsed(elapsed_seconds)
        return by_attempts if _PATH_ORDER[by_attempts] >= _PATH_ORDER[by_time] else by_time

    @staticmethod
    def _path_from_attempts(attempts_failed: int) -> EscalationPath:
        if attempts_failed >= 2:
            return EscalationPath.FAILSAFE
        if attempts_failed == 1:
            return EscalationPath.FAST
        return EscalationPath.NORMAL

    @staticmethod
    def _path_from_elapsed(elapsed_seconds: float) -> EscalationPath:
        if elapsed_seconds >= CONFIG["failsafe_elapsed_seconds"]:
            return EscalationPath.FAILSAFE
        if elapsed_seconds >= CONFIG["fast_elapsed_seconds"]:
            return EscalationPath.FAST
        return EscalationPath.NORMAL
