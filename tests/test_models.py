"""
Quick sanity check for models.py. Not a pytest suite -- just a script you run
from the repo root:

    python3 -m tests.test_models

(1) builds one valid Incident and prints it as JSON
(2) tries to build a VisionFinding from garbage and shows the validation error
"""

from datetime import datetime, timezone

from pydantic import ValidationError

from models import (
    AnomalyEvent,
    FaultClass,
    Incident,
    IncidentStatus,
    RemediationAction,
    RemediationProposal,
    Severity,
    VisionFinding,
)


def build_valid_incident() -> Incident:
    anomaly = AnomalyEvent(
        fault_class=FaultClass.ENCODER_OVERLOAD,
        severity=Severity.HIGH,
        reason="fps 18 < 24 and dropped_frames 8.2% >= 5% for 3 consecutive samples",
        health_value=0,
        telemetry_snapshot={
            "media_fps": 18.0,
            "media_dropped_frames_percent": 8.2,
            "media_cpu_usage_percent": 97.0,
        },
        breach_count=3,
    )
    incident = Incident(anomaly=anomaly, status=IncidentStatus.DECIDING, current_step="decide")
    incident.proposal = RemediationProposal(
        incident_id=incident.incident_id,
        action=RemediationAction.RESTART_ENCODER,
        rationale="Encoder overload; restarting the encoder clears the fault layer.",
        confidence=0.82,
        model="gemini-2.0-flash",
        similar_incident_ids=["hist-001", "hist-004"],
    )
    incident.notes.append("detected by threshold+persistence; proposal from remediation agent")
    return incident


def main() -> None:
    print("=" * 70)
    print("(1) A valid Incident")
    print("=" * 70)
    incident = build_valid_incident()
    print(incident.model_dump_json(indent=2))

    print()
    print("=" * 70)
    print("(2) VisionFinding from garbage data -> ValidationError")
    print("=" * 70)
    garbage = {
        "source": "vision",
        "frame_captured_at": "not-a-timestamp",
        "symptom": "EVERYTHING_IS_FINE",   # not in the VisionSymptom enum
        "description": "",                  # violates min_length=1
        "confidence": 1.7,                  # must be <= 1.0
        "model": "gemini-2.0-flash",
        "made_up_field": "should not be here",  # extra="forbid"
    }
    try:
        VisionFinding(**garbage)
        print("FAIL: expected a ValidationError, got none")
    except ValidationError as exc:
        print(f"{exc.error_count()} validation errors for VisionFinding\n")
        for err in exc.errors():
            loc = ".".join(str(p) for p in err["loc"]) or "(model)"
            print(f"  - {loc}: {err['msg']}  [{err['type']}]")


if __name__ == "__main__":
    main()
