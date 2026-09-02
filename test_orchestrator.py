from __future__ import annotations

import threading
import time
import unittest
import json
from datetime import datetime, timezone

from models import (
    AnomalyEvent,
    ExecutionResult,
    FaultClass,
    Incident,
    IncidentStatus,
    InfraFinding,
    RemediationAction,
    RemediationProposal,
    SafetyCheck,
    SafetyDecision,
    SafetyVerdict,
    Severity,
    VerificationResult,
    VerificationVerdict,
    VisionFinding,
    VisionSymptom,
)
from orchestrator import Orchestrator, read_active_video_section


class Recorder:
    def __init__(self, incident):
        self.incident = incident.model_copy(deep=True)
        self.saved = []

    def load(self, incident_id):
        if incident_id != self.incident.incident_id:
            raise KeyError(incident_id)
        return self.incident.model_copy(deep=True)

    def save(self, incident):
        self.incident = incident.model_copy(deep=True)
        self.saved.append(incident.model_copy(deep=True))


def incident():
    return Incident(
        anomaly=AnomalyEvent(
            fault_class=FaultClass.ENCODER_OVERLOAD,
            severity=Severity.HIGH,
            reason="health zero",
            health_value=0,
            telemetry_snapshot={"media_pipeline_health": 0.0},
            breach_count=5,
        )
    )


def vision(_incident, *, fault):
    return VisionFinding(
        frame_captured_at=datetime.now(timezone.utc),
        symptom=VisionSymptom.MACROBLOCKING,
        description="blocky",
        confidence=0.95,
        model="real-seam",
        raw_response="{}",
    )


def infra(_incident):
    return InfraFinding(
        fault_class=FaultClass.ENCODER_OVERLOAD,
        affected_component="encoder_01",
        description="overloaded",
        supporting_metrics={"media_pipeline_health": 0.0},
        confidence=0.95,
        model="real-seam",
        raw_response="{}",
    )


class Harness:
    def __init__(self, verify_verdict=VerificationVerdict.RECOVERED, gate_allow=True):
        self.recorder = Recorder(incident())
        self.calls = []
        self.verify_verdict = verify_verdict
        self.gate_allow = gate_allow

    def remediate(self, evidence, precedent):
        self.calls.append("remediate")
        return RemediationProposal(
            incident_id=evidence.incident_id,
            action=RemediationAction.RESTART_ENCODER,
            rationale="restart",
            confidence=0.95,
            model="real-seam",
        )

    def gate(self, incident_id):
        self.calls.append("gate")
        inc = self.recorder.load(incident_id)
        verdict = SafetyVerdict.ALLOW if self.gate_allow else SafetyVerdict.BLOCK
        decision = SafetyDecision(
            incident_id=incident_id,
            action=inc.proposal.action,
            verdict=verdict,
            checks=[SafetyCheck(name="policy", passed=self.gate_allow)],
            block_reason=None if self.gate_allow else "policy: blocked",
            idempotency_key="key-1",
        )
        inc.safety_decision = decision
        inc.idempotency_key = decision.idempotency_key
        self.recorder.save(inc)
        return decision

    def control(self, incident_id):
        self.calls.append("control")
        inc = self.recorder.load(incident_id)
        result = ExecutionResult(
            incident_id=incident_id,
            action=inc.proposal.action,
            idempotency_key=inc.idempotency_key,
            success=True,
            detail="called",
        )
        inc.execution = result
        inc.actions_attempted.append(result.action)
        inc.attempt_count += 1
        self.recorder.save(inc)
        return result

    def verify(self, incident_id):
        self.calls.append("verify")
        inc = self.recorder.load(incident_id)
        recovered = self.verify_verdict is VerificationVerdict.RECOVERED
        result = VerificationResult(
            incident_id=incident_id,
            action=inc.execution.action,
            verdict=self.verify_verdict,
            recovered=recovered,
            telemetry_ok=recovered,
            video_ok=recovered,
            health_value=1 if recovered else None,
            stable_window_seconds=15 if recovered else 0,
            failed_checks=[] if recovered else ["telemetry unavailable"],
        )
        inc.verification = result
        self.recorder.save(inc)
        return result

    def fallback(self, incident_id):
        self.calls.append("fallback")
        inc = self.recorder.load(incident_id)
        result = VerificationResult(
            incident_id=incident_id,
            action=RemediationAction.REDUCE_PROFILE,
            verdict=VerificationVerdict.RECOVERED,
            recovered=True,
            telemetry_ok=True,
            video_ok=True,
            health_value=1,
            stable_window_seconds=15,
        )
        inc.verification = result
        inc.actions_attempted.append(RemediationAction.REDUCE_PROFILE)
        inc.attempt_count += 1
        inc.fallback_attempted = True
        self.recorder.save(inc)
        return result

    def report(self, incident_id):
        self.calls.append("report")
        inc = self.recorder.load(incident_id)
        inc.report_sent = True
        self.recorder.save(inc)

    def writeback(self, incident_id):
        self.calls.append("writeback")
        inc = self.recorder.load(incident_id)
        inc.kb_writeback_id = "incident-" + incident_id
        self.recorder.save(inc)

    def orchestrator(self):
        return Orchestrator(
            self.recorder,
            vision_runner=vision,
            infra_runner=infra,
            retrieve_runner=lambda evidence: [],
            remediation_runner=self.remediate,
            gate_runner=self.gate,
            control_runner=self.control,
            verify_runner=self.verify,
            fallback_runner=self.fallback,
            report_runner=self.report,
            writeback_runner=self.writeback,
            active_section_reader=lambda: "overload",
            sleeper=lambda seconds: self.calls.append("cooldown_wait"),
        )


class OrchestratorTests(unittest.TestCase):
    def test_active_fault_state_selects_video_section(self):
        class Response:
            def __init__(self, payload):
                self._body = json.dumps(payload).encode()

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return self._body

        expected = {
            "encoder_overload": "overload",
            "encoder_failure": "failure",
            "network_degradation": "healthy",
        }
        for mode, section in expected.items():
            with self.subTest(mode=mode):
                actual = read_active_video_section(
                    opener=lambda *_args, **_kwargs: Response(
                        {
                            "failure_mode": mode,
                            "fps": 18,
                            "dropped_frames": 8,
                            "packet_loss": 0.1,
                            "encoder_status": 1,
                        }
                    )
                )
                self.assertEqual(actual, section)

        healthy = read_active_video_section(
            opener=lambda *_args, **_kwargs: Response(
                {
                    "failure_mode": "encoder_overload",  # stale display label
                    "fps": 30,
                    "dropped_frames": 0.2,
                    "packet_loss": 0.1,
                    "encoder_status": 1,
                }
            )
        )
        self.assertEqual(healthy, "healthy")

    def test_unknown_or_missing_active_fault_fails_closed(self):
        class Response:
            def __init__(self, payload):
                self.payload = payload

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return None

            def read(self):
                return json.dumps(self.payload).encode()

        bad_health = {
            "failure_mode": "surprise",
            "fps": 18,
            "dropped_frames": 8,
            "packet_loss": 0.1,
            "encoder_status": 1,
        }
        for payload in ({}, bad_health):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                read_active_video_section(
                    opener=lambda *_args, **_kwargs: Response(payload)
                )

    def test_recovered_path_reports_writes_kb_and_closes(self):
        h = Harness()
        final = h.orchestrator().run(h.recorder.incident.incident_id)
        self.assertEqual(final.status, IncidentStatus.CLOSED)
        self.assertTrue(final.report_sent)
        self.assertIsNotNone(final.kb_writeback_id)
        self.assertEqual(
            h.calls,
            ["remediate", "gate", "control", "verify", "report", "writeback"],
        )

    def test_gate_block_is_terminal_and_never_executes(self):
        h = Harness(gate_allow=False)
        final = h.orchestrator().run(h.recorder.incident.incident_id)
        self.assertEqual(final.status, IncidentStatus.BLOCKED)
        self.assertEqual(h.calls, ["remediate", "gate"])
        self.assertIsNone(final.execution)

    def test_cannot_verify_stops_before_reporting(self):
        h = Harness(verify_verdict=VerificationVerdict.CANNOT_VERIFY)
        final = h.orchestrator().run(h.recorder.incident.incident_id)
        self.assertEqual(final.status, IncidentStatus.CANNOT_VERIFY)
        self.assertNotIn("report", h.calls)
        self.assertNotIn("writeback", h.calls)

    def test_recovery_failed_waits_then_uses_one_fallback(self):
        h = Harness(verify_verdict=VerificationVerdict.RECOVERY_FAILED)
        final = h.orchestrator().run(h.recorder.incident.incident_id)
        self.assertEqual(final.status, IncidentStatus.CLOSED)
        self.assertEqual(h.calls.count("fallback"), 1)
        self.assertIn("cooldown_wait", h.calls)
        self.assertEqual(final.attempt_count, 2)

    def test_vision_and_infra_compute_in_parallel(self):
        threads = []

        def slow_vision(inc, *, fault):
            threads.append(threading.current_thread().name)
            time.sleep(0.05)
            return vision(inc, fault=fault)

        def slow_infra(inc):
            threads.append(threading.current_thread().name)
            time.sleep(0.05)
            return infra(inc)

        h = Harness()
        orch = h.orchestrator()
        orch._vision = slow_vision
        orch._infra = slow_infra
        started = time.monotonic()
        orch._parallel_diagnosis(h.recorder.incident)
        elapsed = time.monotonic() - started
        self.assertEqual(len(set(threads)), 2)
        self.assertLess(elapsed, 0.09)

    def test_unexpected_error_is_persisted_failed(self):
        h = Harness()
        orch = h.orchestrator()
        orch._retrieve = lambda evidence: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertRaisesRegex(RuntimeError, "boom"):
            orch.run(h.recorder.incident.incident_id)
        self.assertEqual(h.recorder.incident.status, IncidentStatus.FAILED)
        self.assertTrue(any("FAILED during retrieve" in n for n in h.recorder.incident.notes))


if __name__ == "__main__":
    unittest.main(verbosity=2)
