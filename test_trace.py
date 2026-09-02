import unittest

from models import (
    AnomalyEvent, FaultClass, Incident, IncidentStatus, Severity,
    VisionFinding, VisionSymptom,
)
from trace import render_trace


class TraceTests(unittest.TestCase):
    def test_trace_contains_complete_sections_and_final_reason(self):
        incident = Incident(
            incident_id="trace-1",
            anomaly=AnomalyEvent(fault_class=FaultClass.UNKNOWN, severity=Severity.HIGH,
                                 reason="health stayed zero", health_value=0, breach_count=3),
            vision=VisionFinding(frame_captured_at="2026-01-01T00:00:00Z",
                                 symptom=VisionSymptom.MACROBLOCKING,
                                 description="blocky", confidence=.9, model="test"),
            status=IncidentStatus.BLOCKED,
            current_step="gate",
            terminal_step="gate",
            terminal_reason="compatibility: action is incompatible",
        )
        output = render_trace(incident)
        for heading in ("TIMELINE", "DIAGNOSIS", "RETRIEVED PRECEDENT", "PROPOSAL",
                        "SAFETY GATE", "EXECUTION", "VERIFICATION",
                        "FALLBACK / OUTPUTS", "FINAL OUTCOME"):
            self.assertIn(heading, output)
        self.assertIn("Status: BLOCKED", output)
        self.assertIn("compatibility: action is incompatible", output)


if __name__ == "__main__":
    unittest.main()
