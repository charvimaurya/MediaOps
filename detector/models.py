"""
Incident data model for the deterministic Incident Detector. No AI, no
I/O -- just the shapes detector/rules.py, detector/prometheus.py, and
detector/detector.py pass around.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class IncidentType(str, Enum):
    ENCODER_FAILURE = "encoder_failure"
    NETWORK_DEGRADATION = "network_degradation"
    ENCODER_OVERLOAD = "encoder_overload"


class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class IncidentStatus(str, Enum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"


@dataclass
class Incident:
    incident_id: str
    type: IncidentType
    severity: Severity
    reason: str
    status: IncidentStatus
    created_at: datetime
    resolved_at: Optional[datetime]
    telemetry_snapshot: dict
    breach_count: int
    # Not in the original field list -- needed so the "3 consecutive
    # healthy samples" clearing behavior (still OPEN, no resolved_at) has
    # somewhere to record itself. See detector/detector.py's
    # _mark_candidates_clear().
    candidate_clear: bool = False

    def to_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "type": self.type.value,
            "severity": self.severity.value,
            "reason": self.reason,
            "status": self.status.value,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "telemetry_snapshot": self.telemetry_snapshot,
            "breach_count": self.breach_count,
            "candidate_clear": self.candidate_clear,
        }
