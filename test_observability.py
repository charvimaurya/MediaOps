import io
import json
import logging
import unittest

from models import AnomalyEvent, FaultClass, Incident, Severity
from observability import log_event, record_event


class ObservabilityTests(unittest.TestCase):
    def test_structured_log_always_has_required_fields(self):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("unit_component")
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            log_event("unit_component", "incident-1", "gate", "allow")
        finally:
            logger.removeHandler(handler)
        payload = json.loads(stream.getvalue())
        self.assertEqual(
            set(payload),
            {"timestamp", "component", "incident_id", "step", "action", "outcome", "detail"},
        )
        self.assertEqual(payload["incident_id"], "incident-1")

    def test_record_event_is_durable_model_data(self):
        incident = Incident(anomaly=AnomalyEvent(
            fault_class=FaultClass.UNKNOWN, severity=Severity.HIGH,
            reason="test anomaly", health_value=0, breach_count=1,
        ))
        record_event(incident, "orchestrator", "diagnose", "started", status="DIAGNOSING")
        restored = Incident.model_validate(incident.model_dump(mode="json"))
        self.assertEqual(restored.lifecycle_events[0].step, "diagnose")


if __name__ == "__main__":
    unittest.main()
