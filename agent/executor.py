"""
RealActionExecutor: maps action names to simulator/control.py's
PipelineControl methods, times execution, and returns an ActionResult
whose `executed` field reflects whether the control call actually
succeeded -- not just whether it ran. A failed control call is recorded
as a failed attempt and is never retried by this executor; the state
machine (agent/master_agent.py, unchanged) is what escalates to the next
permitted action.

Implements the existing ActionExecutor interface without changing its
signature.
"""

import logging
import time
from datetime import datetime, timezone
from typing import Optional

from detector.models import Incident
from agent.remediation import ActionExecutor, ActionResult
from agent.metrics import ACTION_DURATION_SECONDS
from simulator.control import PipelineControl

logger = logging.getLogger(__name__)


class RealActionExecutor(ActionExecutor):
    def __init__(self, control: Optional[PipelineControl] = None):
        self._control = control or PipelineControl()
        self._action_map = {
            "restart_encoder": self._control.restart_encoder,
            "reduce_bitrate": self._control.reduce_bitrate,
            "switch_backup": self._control.switch_backup,
            "failover": self._control.failover,
        }

    def execute(self, action: str, incident: Incident) -> ActionResult:
        started_at = datetime.now(timezone.utc)
        start_monotonic = time.monotonic()

        fn = self._action_map.get(action)
        if fn is None:
            completed_at = datetime.now(timezone.utc)
            logger.error("RealActionExecutor: unknown action %r for incident %s", action, incident.incident_id)
            return ActionResult(
                action=action, started_at=started_at, completed_at=completed_at,
                executed=False, detail=f"unknown action {action!r}",
            )

        # An exception inside remediation must never take down the agent
        # or the simulator -- it becomes a failed attempt.
        try:
            success, detail = fn()
        except Exception as exc:
            logger.exception("RealActionExecutor: exception executing %r for incident %s", action, incident.incident_id)
            success, detail = False, f"exception during execution: {exc}"

        duration = time.monotonic() - start_monotonic
        ACTION_DURATION_SECONDS.labels(action=action).observe(duration)
        completed_at = datetime.now(timezone.utc)

        logger.info(
            "RealActionExecutor: %s %s in %.2fs -- %s",
            action, "succeeded" if success else "FAILED", duration, detail,
        )

        return ActionResult(
            action=action, started_at=started_at, completed_at=completed_at, executed=success, detail=detail
        )
