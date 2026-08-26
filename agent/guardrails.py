"""
Full guardrail engine. Absorbs agent/policy.py's action table (imported,
not duplicated) and adds: MAX_ATTEMPTS per incident, per-action cooldowns
tracked across incidents, a system-wide in-flight lock, a (currently
empty) prohibited-action list, and a confidence floor.

check() is the single public entry point -- MasterAgent's GUARDRAIL_CHECK
stage calls it. If it returns allowed=True, it has ALSO started the
cooldown window, recorded the attempt, and acquired the in-flight lock on
the caller's behalf; callers MUST call release() once the action has
actually finished executing (RealActionExecutor does this), whether it
succeeded or failed, so the lock doesn't stay held forever.

failover is exempt from the cooldown (per the brief) and, necessarily,
from MAX_ATTEMPTS too: FAILED_SAFE is reached specifically because
MAX_ATTEMPTS was hit, so failover would be unreachable if it were subject
to the same cap. It is still subject to the confidence floor, exactly as
specified ("never restart_encoder or failover" under low confidence).
"""

import threading
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from detector.models import Incident
from agent import policy
from agent.diagnosis import Diagnosis
from agent.metrics import GUARDRAIL_DENIALS_TOTAL

DEFAULT_COOLDOWN_SECONDS = 10.0
CONFIDENCE_FLOOR = 0.4
NON_DESTRUCTIVE_ACTIONS = ["reduce_bitrate", "switch_backup"]
PROHIBITED_ACTIONS: List[str] = []  # empty for now, wired in


@dataclass
class GuardrailDecision:
    allowed: bool
    reason: str
    rule: str


class GuardrailEngine:
    def __init__(self, cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS):
        self._cooldown_seconds = cooldown_seconds
        self._lock = threading.Lock()  # protects internal bookkeeping below
        self._in_flight = threading.Lock()  # system-wide: one action at a time
        self._last_run: Dict[str, float] = {}  # action -> monotonic timestamp, GLOBAL across incidents
        self._attempts_per_incident: Dict[str, int] = {}

    def reset(self) -> None:
        """Test/demo helper."""
        with self._lock:
            self._last_run = {}
            self._attempts_per_incident = {}
        if self._in_flight.locked():
            self._in_flight.release()

    def check(self, action: str, incident: Incident, diagnosis: Optional[Diagnosis]) -> GuardrailDecision:
        with self._lock:
            if action in PROHIBITED_ACTIONS:
                return self._deny(action, f"{action!r} is on the prohibited-action list", "prohibited_action")

            allowed_for_type = policy.allowed_actions(incident.type)
            if not (action in allowed_for_type or action == policy.FAILSAFE_ACTION):
                return self._deny(
                    action, f"{action!r} is not permitted for {incident.type.value}", "allowed_actions"
                )

            is_failsafe = action == policy.FAILSAFE_ACTION

            attempts = self._attempts_per_incident.get(incident.incident_id, 0)
            if not is_failsafe and attempts >= policy.MAX_ATTEMPTS:
                return self._deny(
                    action, f"MAX_ATTEMPTS ({policy.MAX_ATTEMPTS}) reached for incident {incident.incident_id}", "max_attempts"
                )

            if (
                diagnosis is not None
                and diagnosis.confidence < CONFIDENCE_FLOOR
                and action not in NON_DESTRUCTIVE_ACTIONS
            ):
                return self._deny(
                    action,
                    f"confidence {diagnosis.confidence} below floor {CONFIDENCE_FLOOR}; "
                    f"only non-destructive actions permitted",
                    "confidence_floor",
                )

            if not is_failsafe:
                last_run = self._last_run.get(action)
                if last_run is not None:
                    since = time.monotonic() - last_run
                    if since < self._cooldown_seconds:
                        return self._deny(
                            action, f"{action!r} is in cooldown for {self._cooldown_seconds - since:.1f}s more", "cooldown"
                        )

            if not self._in_flight.acquire(blocking=False):
                return self._deny(action, "another remediation action is currently in flight", "in_flight_lock")

            # allowed -- commit bookkeeping
            self._last_run[action] = time.monotonic()
            self._attempts_per_incident[incident.incident_id] = attempts + 1

            return GuardrailDecision(allowed=True, reason=f"{action!r} permitted", rule="approved")

    def release(self) -> None:
        """Must be called once, after an approved action finishes
        executing (pass or fail), to free the in-flight lock."""
        if self._in_flight.locked():
            self._in_flight.release()

    @staticmethod
    def _deny(action: str, reason: str, rule: str) -> GuardrailDecision:
        GUARDRAIL_DENIALS_TOTAL.labels(rule=rule, action=action).inc()
        return GuardrailDecision(allowed=False, reason=reason, rule=rule)
