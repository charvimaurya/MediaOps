"""Unit tests for the preview-first stale incident cleanup helper."""

from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone

from tools import cleanup_incidents
from models import AnomalyEvent, FaultClass, Incident, IncidentStatus, Severity


NOW = datetime(2026, 9, 2, 12, 0, tzinfo=timezone.utc)


def incident(status: IncidentStatus, age_hours: float) -> Incident:
    stamp = NOW - timedelta(hours=age_hours)
    return Incident(
        status=status,
        current_step="test",
        created_at=stamp,
        updated_at=stamp,
        anomaly=AnomalyEvent(
            detected_at=stamp,
            fault_class=FaultClass.UNKNOWN,
            severity=Severity.HIGH,
            reason="test",
            health_value=0,
            telemetry_snapshot={},
            breach_count=1,
        ),
    )


class FakeSnapshot:
    def __init__(self, document_id: str, data: dict | None, exists: bool = True) -> None:
        self.id = document_id
        self._data = data
        self.exists = exists

    def to_dict(self):
        return self._data


class FakeReference:
    def __init__(self, collection: "FakeCollection", document_id: str) -> None:
        self.collection = collection
        self.document_id = document_id

    def get(self) -> FakeSnapshot:
        data = self.collection.documents.get(self.document_id)
        return FakeSnapshot(self.document_id, data, exists=data is not None)

    def delete(self) -> None:
        self.collection.deleted.append(self.document_id)
        del self.collection.documents[self.document_id]


class FakeCollection:
    id = "incidents"

    def __init__(self, documents: dict[str, dict]) -> None:
        self.documents = documents
        self.deleted: list[str] = []

    def stream(self):
        return [FakeSnapshot(key, value) for key, value in self.documents.items()]

    def document(self, document_id: str) -> FakeReference:
        return FakeReference(self, document_id)


class FakeRecorder:
    def __init__(self, documents: dict[str, dict]) -> None:
        self._col = FakeCollection(documents)


class CleanupIncidentTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stale = incident(IncidentStatus.DETECTED, 48)
        self.fresh = incident(IncidentStatus.DIAGNOSING, 1)
        self.terminal = incident(IncidentStatus.RESOLVED, 72)
        self.recorder = FakeRecorder({
            self.stale.incident_id: self.stale.model_dump(mode="json"),
            self.fresh.incident_id: self.fresh.model_dump(mode="json"),
            self.terminal.incident_id: self.terminal.model_dump(mode="json"),
            "malformed": {"status": "DETECTED"},
        })

    def test_scan_selects_only_stale_non_terminal_incidents(self) -> None:
        result = cleanup_incidents.scan_stale_incidents(
            self.recorder, older_than=timedelta(hours=24), now=NOW
        )
        self.assertEqual([item.incident_id for item in result.candidates], [self.stale.incident_id])
        self.assertEqual([item[0] for item in result.malformed], ["malformed"])

    def test_preview_and_cancel_delete_nothing(self) -> None:
        output = io.StringIO()
        with redirect_stdout(output):
            code = cleanup_incidents.main(
                ["--older-than-hours", "24"], recorder_factory=lambda: self.recorder,
                now=NOW,
            )
        self.assertEqual(code, 0)
        self.assertEqual(self.recorder._col.deleted, [])
        self.assertIn("PREVIEW ONLY: nothing deleted", output.getvalue())

        with redirect_stdout(io.StringIO()):
            code = cleanup_incidents.main(
                ["--older-than-hours", "24", "--delete"],
                recorder_factory=lambda: self.recorder,
                input_fn=lambda prompt: "no",
                now=NOW,
            )
        self.assertEqual(code, 1)
        self.assertEqual(self.recorder._col.deleted, [])

    def test_exact_confirmation_deletes_only_previewed_candidate(self) -> None:
        with redirect_stdout(io.StringIO()):
            code = cleanup_incidents.main(
                ["--older-than-hours", "24", "--delete"],
                recorder_factory=lambda: self.recorder,
                input_fn=lambda prompt: "DELETE 1",
                now=NOW,
            )
        self.assertEqual(code, 0)
        self.assertEqual(self.recorder._col.deleted, [self.stale.incident_id])
        self.assertIn(self.fresh.incident_id, self.recorder._col.documents)
        self.assertIn(self.terminal.incident_id, self.recorder._col.documents)
        self.assertIn("malformed", self.recorder._col.documents)

    def test_changed_document_is_rechecked_and_skipped(self) -> None:
        result = cleanup_incidents.scan_stale_incidents(
            self.recorder, older_than=timedelta(hours=24), now=NOW
        )
        changed = self.stale.model_copy(deep=True)
        changed.updated_at = NOW
        changed.status = IncidentStatus.DIAGNOSING
        self.recorder._col.documents[self.stale.incident_id] = changed.model_dump(mode="json")

        deleted, skipped = cleanup_incidents.delete_candidates(
            self.recorder, result.candidates
        )
        self.assertEqual(deleted, [])
        self.assertEqual(skipped[0][0], self.stale.incident_id)
        self.assertIn("changed after preview", skipped[0][1])

    def test_negative_age_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cleanup_incidents.scan_stale_incidents(
                self.recorder, older_than=timedelta(hours=-1), now=NOW
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
