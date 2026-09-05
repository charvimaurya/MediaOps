from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException

from models import (
    AnomalyEvent,
    ExecutionResult,
    FaultClass,
    Incident,
    IncidentStatus,
    RemediationAction,
    Severity,
    VerificationResult,
    VerificationVerdict,
)
from pdf_report import PDFReportError, build_incident_pdf
from web.ops_console import download_incident_report


def recovered_incident() -> Incident:
    now = datetime.now(timezone.utc)
    incident = Incident(
        incident_id="pdf-report-test",
        anomaly=AnomalyEvent(
            detected_at=now - timedelta(seconds=75),
            fault_class=FaultClass.UNKNOWN,
            severity=Severity.HIGH,
            reason="media_pipeline_health stayed at zero",
            health_value=0,
            breach_count=6,
        ),
        status=IncidentStatus.CLOSED,
        closed_at=now,
        report_sent=True,
        kb_writeback_id="incident-pdf-report-test",
    )
    incident.execution = ExecutionResult(
        incident_id=incident.incident_id,
        action=RemediationAction.RESTART_ENCODER,
        idempotency_key="pdf-key",
        success=True,
        detail="encoder restarted",
    )
    incident.verification = VerificationResult(
        incident_id=incident.incident_id,
        action=RemediationAction.RESTART_ENCODER,
        verdict=VerificationVerdict.RECOVERED,
        recovered=True,
        telemetry_ok=True,
        video_ok=True,
        health_value=1,
        stable_window_seconds=15,
        samples=[{"media_pipeline_health": 1.0, "media_fps": 30.0}],
    )
    return incident


class Recorder:
    def __init__(self, incident: Incident):
        self.incident = incident

    def load(self, incident_id: str) -> Incident:
        if incident_id != self.incident.incident_id:
            raise KeyError(incident_id)
        return self.incident


class PDFReportTests(unittest.TestCase):
    def test_verified_closed_incident_produces_real_pdf(self):
        content = build_incident_pdf(recovered_incident())
        self.assertTrue(content.startswith(b"%PDF-"))
        self.assertGreater(len(content), 3000)

    def test_unverified_or_active_incident_is_refused(self):
        incident = recovered_incident()
        incident.status = IncidentStatus.VERIFYING
        with self.assertRaises(PDFReportError):
            build_incident_pdf(incident)

    def test_download_response_is_pdf_attachment(self):
        incident = recovered_incident()
        response = download_incident_report(incident.incident_id, Recorder(incident))
        self.assertEqual(response.media_type, "application/pdf")
        self.assertIn("mediaops-incident-pdf-report-test.pdf", response.headers["content-disposition"])
        self.assertTrue(response.body.startswith(b"%PDF-"))

    def test_download_route_returns_conflict_for_non_closed_incident(self):
        incident = recovered_incident()
        incident.status = IncidentStatus.BLOCKED
        with self.assertRaises(HTTPException) as raised:
            download_incident_report(incident.incident_id, Recorder(incident))
        self.assertEqual(raised.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
