"""
Diagnosis seam. StubDiagnosisProvider is deterministic; a later phase
swaps in a Gemini-backed implementation behind this same abstract
interface -- nothing else in agent/ should need to change when that
happens.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable, List

from detector.models import Incident, IncidentType
from agent import policy


@dataclass
class Diagnosis:
    root_cause: str
    evidence: List[str]
    confidence: float
    recommended_action: str


class DiagnosisProvider(ABC):
    @abstractmethod
    def diagnose(self, incident: Incident, telemetry: dict, history: list) -> Diagnosis:
        ...


_ROOT_CAUSE = {
    IncidentType.ENCODER_OVERLOAD: "Encoder resource saturation",
    IncidentType.NETWORK_DEGRADATION: "Network path congestion",
    IncidentType.ENCODER_FAILURE: "Encoder process failure",
}

# incident type -> that failure's expected signals: (telemetry key,
# breach predicate, human-readable label). Confidence is the fraction of
# these actually breaching in the telemetry passed in -- never hardcoded.
_SIGNAL_CHECKS = {
    IncidentType.ENCODER_OVERLOAD: [
        ("cpu_usage", lambda v: v > 90, "CPU usage"),
        ("encoding_latency", lambda v: v > 150, "encoding latency"),
        ("fps", lambda v: v < 24, "FPS"),
        ("dropped_frames", lambda v: v > 5, "dropped frames"),
    ],
    IncidentType.NETWORK_DEGRADATION: [
        ("packet_loss", lambda v: v > 2.0, "packet loss"),
        ("network_latency", lambda v: v > 150, "network latency"),
    ],
    IncidentType.ENCODER_FAILURE: [
        ("encoder_status", lambda v: v == 0, "encoder status"),
        ("fps", lambda v: v == 0, "FPS"),
        ("bitrate", lambda v: v == 0, "bitrate"),
    ],
}


class StubDiagnosisProvider(DiagnosisProvider):
    """recommended_action is always drawn from allowed_actions_fn(type) --
    the same constraint a real (e.g. Gemini-backed) provider will need to
    satisfy later. Injected via the constructor rather than the diagnose()
    method so the abstract interface stays exactly as specified."""

    def __init__(self, allowed_actions_fn: Callable[[IncidentType], List[str]] = policy.allowed_actions):
        self._allowed_actions_fn = allowed_actions_fn

    def diagnose(self, incident: Incident, telemetry: dict, history: list) -> Diagnosis:
        incident_type = incident.type
        checks = _SIGNAL_CHECKS.get(incident_type, [])

        evidence = []
        breaching = 0
        for key, predicate, label in checks:
            value = telemetry.get(key)
            if value is None:
                continue
            if predicate(value):
                breaching += 1
                evidence.append(f"{label} is {value} (breaching)")
            else:
                evidence.append(f"{label} is {value} (normal)")

        confidence = round(breaching / len(checks), 2) if checks else 0.0

        allowed = self._allowed_actions_fn(incident_type)
        recommended_action = allowed[0] if allowed else policy.FAILSAFE_ACTION

        return Diagnosis(
            root_cause=_ROOT_CAUSE.get(incident_type, "Unknown"),
            evidence=evidence,
            confidence=confidence,
            recommended_action=recommended_action,
        )
