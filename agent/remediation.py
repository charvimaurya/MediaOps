"""
Remediation stub. MUST NOT touch the simulator, clear the injected
failure, or change any telemetry -- it exists purely to exercise the
ACTING/VERIFYING path of the state machine before real remediation lands.
"""

import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone

from detector.models import Incident

logger = logging.getLogger(__name__)

DEFAULT_EXECUTE_SECONDS = 0.3


@dataclass
class ActionResult:
    action: str
    started_at: datetime
    completed_at: datetime
    executed: bool
    detail: str


class ActionExecutor(ABC):
    @abstractmethod
    def execute(self, action: str, incident: Incident) -> ActionResult:
        ...


class StubActionExecutor(ActionExecutor):
    def __init__(self, duration_seconds: float = DEFAULT_EXECUTE_SECONDS):
        self._duration_seconds = duration_seconds

    def execute(self, action: str, incident: Incident) -> ActionResult:
        started_at = datetime.now(timezone.utc)
        logger.info("Executing stub action %r for incident %s (no real effect)", action, incident.incident_id)
        if self._duration_seconds > 0:
            time.sleep(self._duration_seconds)
        completed_at = datetime.now(timezone.utc)
        return ActionResult(
            action=action,
            started_at=started_at,
            completed_at=completed_at,
            executed=True,
            detail=f"stub execution of {action!r}; no real change made",
        )
