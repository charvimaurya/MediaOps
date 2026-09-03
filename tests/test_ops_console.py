from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from web import ops_console
from models import AnomalyEvent, FaultClass, Incident, Severity


def incident(incident_id: str, age_seconds: int) -> Incident:
    now = datetime.now(timezone.utc)
    return Incident(
        incident_id=incident_id,
        anomaly=AnomalyEvent(
            fault_class=FaultClass.UNKNOWN,
            severity=Severity.HIGH,
            reason="health stayed at zero",
            health_value=0,
            breach_count=3,
        ),
        updated_at=now - timedelta(seconds=age_seconds),
    )


class Snapshot:
    def __init__(self, value):
        self.value = value

    def to_dict(self):
        return self.value


class Collection:
    def __init__(self, values):
        self.values = values

    def stream(self):
        return [Snapshot(value) for value in self.values]


class Recorder:
    def __init__(self, incidents):
        self.incidents = incidents
        self._col = Collection([item.model_dump(mode="json") for item in incidents])

    def load(self, incident_id):
        return next(item for item in self.incidents if item.incident_id == incident_id)


class OpsConsoleTests(unittest.TestCase):
    def test_each_button_maps_only_to_predefined_simulator_endpoint(self):
        expected = {
            "encoder-overload": "/failure/encoder-overload",
            "encoder-failure": "/failure/encoder-crash",
            "network-degradation": "/failure/network-degradation",
            "reset": "/recovery/reset",
        }
        for fault, path in expected.items():
            with self.subTest(fault=fault), patch.object(
                ops_console, "simulator_request", return_value={"ok": True}
            ) as request:
                self.assertEqual(ops_console.inject_fault(fault), {"ok": True})
                request.assert_called_once_with(path, method="POST")

    def test_latest_incident_uses_most_recent_firestore_update(self):
        old = incident("old", 20)
        new = incident("new", 2)
        self.assertEqual(ops_console.latest_incident(Recorder([old, new])).incident_id, "new")

    def test_incident_response_serializes_existing_contract(self):
        item = incident("incident-1", 0)
        payload = ops_console.read_incident(item.incident_id, Recorder([item]))
        self.assertEqual(payload["incident"]["incident_id"], "incident-1")
        self.assertEqual(payload["incident"]["status"], "DETECTED")


if __name__ == "__main__":
    unittest.main()
