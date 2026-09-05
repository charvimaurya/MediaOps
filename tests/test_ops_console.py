from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from web import ops_console
from fastapi import BackgroundTasks

from models import (
    AnomalyEvent,
    FaultClass,
    Incident,
    IncidentStatus,
    RemediationAction,
    Severity,
    VerificationResult,
    VerificationVerdict,
)


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
    def setUp(self):
        # Each endpoint test starts with no process-wide console workflow.
        with ops_console._workflow_lock:
            ops_console._workflow_active = False
            ops_console._active_incident_id = None

    def test_console_starts_with_guided_welcome_then_has_split_workflow(self):
        page = ops_console.PAGE.read_text()
        self.assertIn('id="landingView"', page)
        self.assertIn('class="welcome hidden" id="welcomeView"', page)
        self.assertIn('data-try-now', page)
        self.assertIn("Autonomous incident response for live video", page)
        self.assertIn("An alert tells you something's wrong", page)
        self.assertIn("Built for streaming and broadcast operators", page)
        self.assertIn("AI proposes, deterministic code decides.", page)
        self.assertIn("Detect", page)
        self.assertIn("Diagnose", page)
        self.assertIn("Fix", page)
        self.assertIn("Verify", page)
        self.assertIn("function showLanding()", page)
        self.assertIn("button.onclick=showWelcome", page)
        self.assertIn("See it in action — choose a fault to simulate:", page)
        self.assertIn("before viewers notice", page)
        self.assertIn('id="workflowView"', page)
        self.assertIn('class="split"', page)
        self.assertIn("Back / try another error", page)
        self.assertIn("if(box)box.innerHTML", page)

    def test_workflow_uses_one_current_card_with_subtle_progress(self):
        page = ops_console.PAGE.read_text()
        self.assertIn('class="current-card', page)
        self.assertIn('class="step-progress', page)
        self.assertIn("const stepDescriptions=", page)
        self.assertIn("Deterministic code independently checks", page)
        self.assertIn("Download incident report", page)
        self.assertIn("i.status==='CLOSED'&&i.verification?.verdict==='RECOVERED'", page)

    def test_workflow_renders_real_firestore_evidence_for_decision_steps(self):
        page = ops_console.PAGE.read_text()
        self.assertIn("v.symptom", page)
        self.assertIn("f.affected_component", page)
        self.assertIn("f.supporting_metrics", page)
        self.assertIn("Both agents agree:", page)
        self.assertIn("p.rationale", page)
        self.assertIn("d.checks.map", page)
        self.assertIn("c.detail", page)
        self.assertIn("v.samples", page)
        self.assertIn("v.vision_recheck.symptom", page)
        self.assertIn("presentedStep<target", page)
        self.assertIn("media_cpu_usage_percent", page)
        self.assertIn("media_encoding_latency_ms", page)
        self.assertIn("cache:'no-store'", page)
        self.assertIn("incidentPollInFlight", page)
        self.assertIn("requestedId!==liveIncidentId", page)
        self.assertIn("if(awaitingIncidentSince)pollHistory()", page)
        self.assertIn("if(completedStatuses.has(i.status)){liveIncidentId=null;pollHistory()}", page)
        self.assertIn("else if(completedStatuses.has(i.status))presentedStep=target", page)
        self.assertIn("function precedentEvidence(matches)", page)
        self.assertIn("m.similarity", page)
        self.assertIn("m.action_taken", page)
        self.assertIn("m.outcome", page)
        self.assertIn("No similar past incidents cleared the matching threshold.", page)
        self.assertIn("The system recalls how similar problems were resolved before.", page)
        self.assertIn("Average MTTR", page)
        self.assertNotIn("Auto-resolution rate", page)
        self.assertIn("Recovered in ${fmtDuration(i.mttr_seconds)}", page)
        self.assertNotIn("'Precedent Retrieved'", page)
        self.assertIn("class=\"finding-head\"", page)
        self.assertIn("class=\"metric-grid\"", page)
        self.assertIn("Affected component", page)
        self.assertIn("media_cpu_usage_percent:['CPU','%']", page)
        self.assertIn("media_packet_loss_percent:['Packet loss','%']", page)
        self.assertIn("class=\"result-panel\"", page)
        self.assertIn("A fault was detected and confirmed as sustained", page)
        self.assertIn("Two agents independently investigated", page)
        self.assertIn("Both findings were validated and confirmed to corroborate", page)
        self.assertIn("The fix passed all safety checks and was approved", page)
        self.assertIn("The approved fix was executed", page)
        self.assertIn("Recovery was independently confirmed in both video and metrics", page)
        self.assertIn("View detection signal", page)
        self.assertIn("View safety checks", page)
        self.assertIn("View recovery samples", page)
        self.assertIn("Packet loss ${fmtNumber(s.media_packet_loss_percent", page)
        self.assertNotIn("CPU ${esc(s.media_cpu_usage_percent", page)
        self.assertIn("function fmtSeconds(v)", page)
        self.assertIn("n.toFixed(1)+'s'", page)
        self.assertIn("fmtSeconds(v.stable_window_seconds)", page)
        self.assertNotIn("${esc(v.stable_window_seconds)}s", page)
        self.assertNotIn("Only the Control Plane can execute", page)
        self.assertIn("if(n===8){", page)
        self.assertNotIn("${i.verification?verificationEvidence(i.verification):''}", page)
        self.assertIn("Review full incident", page)
        for section in (
            "Detection",
            "Diagnosis",
            "Evidence",
            "Similar Past Incidents",
            "Proposal",
            "Safety Gate",
            "Execution",
            "Verification",
            "Recovery",
            "Report",
        ):
            self.assertIn(f"['{section}',", page)

    def test_video_stream_is_segment_looped_and_recovery_is_firestore_verified(self):
        page = ops_console.PAGE.read_text()
        self.assertIn('id="liveStream"', page)
        self.assertIn("encoder_overload:{start:5.2,end:10.8", page)
        self.assertIn("encoder_failure:{start:17.2,end:22.7", page)
        self.assertIn("i.verification?.verdict==='RECOVERED'", page)
        self.assertIn("addEventListener('timeupdate',maintainStreamLoop)", page)
        self.assertIn("function scheduleFaultBreak(fault)", page)
        self.assertIn("},2500)", page)
        self.assertIn("state.failure_mode===fault", page)
        self.assertIn("Monitoring stream", page)
        self.assertIn("Confirming sustained fault", page)
        self.assertIn("avoid raising an incident for a brief glitch", page)
        self.assertIn("clearTimeout(faultTransitionTimer)", page)

    def test_grafana_dashboard_is_lazy_loaded_in_evidence_tab(self):
        page = ops_console.PAGE.read_text()
        self.assertIn('id="metricsTab"', page)
        self.assertIn('Infrastructure Metrics · Grafana', page)
        self.assertIn('id="grafanaFrame"', page)
        self.assertIn("await api('/api/config')", page)
        self.assertIn("config.grafana_embed_url", page)
        self.assertIn("selectEvidenceTab('metrics')", page)

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

    def test_fault_request_schedules_existing_workflow_but_reset_does_not(self):
        tasks = BackgroundTasks()
        with patch.object(ops_console, "simulator_request", return_value={"status": "ok"}):
            result = ops_console.inject_fault("encoder-overload", tasks)
        self.assertTrue(result["workflow_started"])
        self.assertEqual(len(tasks.tasks), 1)

        reset_tasks = BackgroundTasks()
        with patch.object(ops_console, "simulator_request", return_value={"status": "ok"}):
            result = ops_console.inject_fault("reset", reset_tasks)
        self.assertFalse(result["workflow_started"])
        self.assertEqual(len(reset_tasks.tasks), 0)

    def test_active_workflow_rejects_fault_before_simulator_is_modified(self):
        with ops_console._workflow_lock:
            ops_console._workflow_active = True
        tasks = BackgroundTasks()
        with patch.object(ops_console, "simulator_request") as request:
            with self.assertRaises(ops_console.HTTPException) as raised:
                ops_console.inject_fault("encoder-failure", tasks)
        self.assertEqual(raised.exception.status_code, 409)
        self.assertIn("already running", raised.exception.detail)
        request.assert_not_called()
        self.assertEqual(len(tasks.tasks), 0)

    def test_failed_fault_injection_releases_workflow_slot(self):
        tasks = BackgroundTasks()
        with patch.object(
            ops_console, "simulator_request", side_effect=RuntimeError("simulator down")
        ):
            with self.assertRaisesRegex(RuntimeError, "simulator down"):
                ops_console.inject_fault("encoder-overload", tasks)
        self.assertFalse(ops_console._workflow_active)
        self.assertIsNone(ops_console._active_incident_id)
        self.assertEqual(len(tasks.tasks), 0)

    def test_health_exposes_current_incident_for_browser_reconnection(self):
        with ops_console._workflow_lock:
            ops_console._workflow_active = True
            ops_console._active_incident_id = "current-incident"
        with patch.object(
            ops_console, "simulator_request", return_value={"status": "ok"}
        ), patch.object(ops_console, "query_health", return_value=0):
            result = ops_console.console_health()
        self.assertTrue(result["workflow_active"])
        self.assertEqual(result["active_incident_id"], "current-incident")

    def test_latest_incident_uses_most_recent_firestore_update(self):
        old = incident("old", 20)
        new = incident("new", 2)
        self.assertEqual(ops_console.latest_incident(Recorder([old, new])).incident_id, "new")

    def test_incident_response_serializes_existing_contract(self):
        item = incident("incident-1", 0)
        payload = ops_console.read_incident(item.incident_id, Recorder([item]))
        self.assertEqual(payload["incident"]["incident_id"], "incident-1")
        self.assertEqual(payload["incident"]["status"], "DETECTED")
        self.assertIsNone(payload["incident"]["mttr_seconds"])

    def test_history_is_persistent_sorted_and_includes_summary_stats(self):
        old = incident("old", 20)
        new = incident("new", 2)
        new.status = IncidentStatus.CLOSED
        new.closed_at = new.created_at + timedelta(seconds=42)
        new.verification = VerificationResult(
            incident_id=new.incident_id,
            verified_at=new.anomaly.detected_at + timedelta(seconds=42),
            action=RemediationAction.RESTART_ENCODER,
            verdict=VerificationVerdict.RECOVERED,
            recovered=True,
            telemetry_ok=True,
            video_ok=True,
            health_value=1,
            stable_window_seconds=15,
        )
        payload = ops_console.read_incidents(Recorder([old, new]))
        self.assertEqual([row["incident_id"] for row in payload["incidents"]], ["new", "old"])
        self.assertEqual(payload["stats"]["total_incidents"], 2)
        self.assertEqual(payload["stats"]["terminal_incidents"], 1)
        self.assertEqual(payload["stats"]["auto_resolved_percent"], 100.0)
        self.assertEqual(payload["stats"]["average_mttr_seconds"], 42.0)
        self.assertEqual(payload["incidents"][0]["mttr_seconds"], 42.0)

    def test_metrics_are_read_from_prometheus_helpers(self):
        raw = {
            "media_fps": 29.97,
            "media_cpu_usage_percent": 45.0,
            "media_packet_loss_percent": 0.2,
            "media_encoder_status": 1.0,
        }
        with patch.object(ops_console, "query_health", return_value=1), patch.object(
            ops_console, "query_raw_snapshot", return_value=raw
        ):
            payload = ops_console.prometheus_metrics()
        self.assertEqual(payload["status"], "HEALTHY")
        self.assertEqual(payload["fps"], 29.97)


if __name__ == "__main__":
    unittest.main()
