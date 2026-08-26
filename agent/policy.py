"""
Data table of permitted remediation actions per incident type, and the
fallback-ordering logic for picking the next one. Deliberately just a
lookup table + simple selection rules -- not a rules engine.
"""

from typing import List, Optional

from detector.models import IncidentType
from agent.aht import EscalationPath

ALLOWED_ACTIONS = {
    IncidentType.ENCODER_OVERLOAD: ["restart_encoder", "switch_backup"],
    IncidentType.NETWORK_DEGRADATION: ["reduce_bitrate", "switch_backup"],
    IncidentType.ENCODER_FAILURE: ["restart_encoder", "switch_backup", "failover"],
}

MAX_ATTEMPTS = 3
FAILSAFE_ACTION = "failover"


def allowed_actions(incident_type: IncidentType) -> List[str]:
    return list(ALLOWED_ACTIONS.get(incident_type, []))


def next_action(incident_type: IncidentType, already_attempted: List[str], path: EscalationPath) -> Optional[str]:
    actions = allowed_actions(incident_type)

    if path == EscalationPath.FAILSAFE:
        # Always the designated fail-safe action, regardless of the
        # per-type list or what's already been tried -- the caller (the
        # state machine) is responsible for not re-executing a repeat.
        return FAILSAFE_ACTION

    if path == EscalationPath.FAST:
        non_failsafe = [a for a in actions if a != FAILSAFE_ACTION]
        if non_failsafe:
            last = non_failsafe[-1]
            if last not in already_attempted:
                return last
        # last non-failover action already tried -- fall through to
        # normal first-untried ordering below

    for action in actions:
        if action not in already_attempted:
            return action

    return None
