"""Deterministic tests for sustained dual-domain recovery verification."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone
from unittest.mock import patch

import verify_recovery as vr
from models import (
    ExecutionResult,
    IncidentStatus,
    VerificationVerdict,
    VisionFinding,
    VisionSymptom,
)
from tests.test_safety_gate import make_incident


CONFIG = vr.VerificationConfig(15.0, 3.0, 0.8)


class Clock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def executable_incident():
    incident = make_incident()
    incident.execution = ExecutionResult(
        incident_id=incident.incident_id,
        action=incident.proposal.action,
        idempotency_key="test-key",
        success=True,
        detail="control call succeeded (not recovery)",
    )
    return incident


def healthy_metrics():
    return {
        "media_pipeline_health": 1.0,
        "media_encoder_status": 1.0,
        "media_fps": 30.0,
        "media_dropped_frames_percent": 0.2,
        "media_packet_loss_percent": 0.1,
    }


def vision(symptom=VisionSymptom.NORMAL, confidence=0.95):
    now = datetime.now(timezone.utc)
    return VisionFinding(
        observed_at=now,
        frame_captured_at=now,
        symptom=symptom,
        description="test video finding",
        confidence=confidence,
        model="test",
        raw_response="{}",
    )


def run(incident, metrics_reader, vision_reader=lambda inc, fault: vision()):
    clock = Clock()
    result = vr.verify_recovery(
        incident,
        config=CONFIG,
        metrics_reader=metrics_reader,
        vision_reader=vision_reader,
        monotonic=clock.monotonic,
        sleep=clock.sleep,
    )
    return result, clock


class VerifyRecoveryTests(unittest.TestCase):
    def test_sustained_metrics_and_normal_video_recover(self):
        calls = {"metrics": 0, "vision_fault": None}

        def metrics():
            calls["metrics"] += 1
            return healthy_metrics()

        def video(incident, fault):
            calls["vision_fault"] = fault
            return vision()

        result, clock = run(executable_incident(), metrics, video)
        self.assertEqual(result.verdict, VerificationVerdict.RECOVERED)
        self.assertTrue(result.recovered)
        self.assertTrue(result.telemetry_ok)
        self.assertTrue(result.video_ok)
        self.assertGreaterEqual(result.stable_window_seconds, 15.0)
        self.assertEqual(calls["metrics"], 6)
        self.assertEqual(calls["vision_fault"], "healthy")
        self.assertEqual(clock.value, 15.0)

    def test_immediate_unhealthy_is_recovery_failed(self):
        broken = healthy_metrics()
        broken["media_pipeline_health"] = 0.0
        broken["media_fps"] = 18.0
        result, _ = run(executable_incident(), lambda: broken)
        self.assertEqual(result.verdict, VerificationVerdict.RECOVERY_FAILED)
        self.assertFalse(result.recovered)
        self.assertIn("telemetry relapse/unhealthy", result.failed_checks[0])

    def test_mid_window_relapse_is_recovery_failed(self):
        readings = [healthy_metrics(), healthy_metrics(), healthy_metrics()]
        relapse = healthy_metrics()
        relapse["media_pipeline_health"] = 0.0
        readings.append(relapse)

        def metrics():
            return readings.pop(0)

        result, clock = run(executable_incident(), metrics)
        self.assertEqual(result.verdict, VerificationVerdict.RECOVERY_FAILED)
        self.assertEqual(len(result.samples), 4)
        self.assertEqual(clock.value, 9.0)

    def test_missing_or_unavailable_metrics_cannot_verify(self):
        missing = healthy_metrics()
        del missing["media_encoder_status"]
        result, _ = run(executable_incident(), lambda: missing)
        self.assertEqual(result.verdict, VerificationVerdict.CANNOT_VERIFY)
        self.assertIn("metrics missing", result.failed_checks[0])

        def unavailable():
            raise RuntimeError("Prometheus down")

        result, _ = run(executable_incident(), unavailable)
        self.assertEqual(result.verdict, VerificationVerdict.CANNOT_VERIFY)
        self.assertIn("Prometheus down", result.failed_checks[0])

    def test_video_fault_or_low_confidence_never_recovers(self):
        result, _ = run(
            executable_incident(),
            healthy_metrics,
            lambda inc, fault: vision(VisionSymptom.MACROBLOCKING),
        )
        self.assertEqual(result.verdict, VerificationVerdict.RECOVERY_FAILED)
        self.assertTrue(result.telemetry_ok)
        self.assertFalse(result.video_ok)
        self.assertIn("domains disagree", result.failed_checks[0])

        result, _ = run(
            executable_incident(),
            healthy_metrics,
            lambda inc, fault: vision(confidence=0.5),
        )
        self.assertEqual(result.verdict, VerificationVerdict.CANNOT_VERIFY)
        self.assertFalse(result.recovered)

    def test_missing_execution_cannot_verify(self):
        result, _ = run(make_incident(), healthy_metrics)
        self.assertEqual(result.verdict, VerificationVerdict.CANNOT_VERIFY)
        self.assertEqual(result.failed_checks, ["execution result is missing"])

    def test_invalid_config_fails_safe(self):
        clock = Clock()
        with patch.dict("os.environ", {"VERIFY_STABLE_WINDOW_SECONDS": "invalid"}):
            result = vr.verify_recovery(
                executable_incident(),
                metrics_reader=healthy_metrics,
                vision_reader=lambda inc, fault: vision(),
                monotonic=clock.monotonic,
                sleep=clock.sleep,
            )
        self.assertEqual(result.verdict, VerificationVerdict.CANNOT_VERIFY)
        self.assertFalse(result.recovered)
        self.assertIn("verification error", result.failed_checks[0])
        with self.assertRaises(ValueError):
            vr.VerificationConfig(0.0, 3.0, 0.8)

    def test_runner_persists_verification_without_resolving(self):
        incident = executable_incident()
        recovered, _ = run(incident, healthy_metrics)

        class Recorder:
            instance = None

            def __init__(self):
                self.saved = []
                Recorder.instance = self

            def load(self, incident_id):
                return incident

            def save(self, value):
                self.saved.append(value.model_copy(deep=True))

        with patch.object(vr, "IncidentRecorder", Recorder), patch.object(
            vr, "verify_recovery", return_value=recovered
        ):
            result = vr.run_for_incident(
                incident.incident_id, settle_seconds=0
            )

        self.assertEqual(result.verdict, VerificationVerdict.RECOVERED)
        self.assertEqual(Recorder.instance.saved[-1].status, IncidentStatus.VERIFYING)
        self.assertEqual(Recorder.instance.saved[-1].verification, recovered)
        self.assertIsNone(Recorder.instance.saved[-1].closed_at)

    def test_runner_waits_before_starting_verification(self):
        incident = executable_incident()
        recovered, _ = run(incident, healthy_metrics)
        calls = []

        class Recorder:
            def load(self, incident_id):
                return incident

            def save(self, value):
                calls.append(("save", value.current_step))

        with patch.object(vr, "verify_recovery", return_value=recovered):
            result = vr.run_for_incident(
                incident.incident_id,
                recorder=Recorder(),
                settle_seconds=12,
                settle_sleep=lambda seconds: calls.append(("sleep", seconds)),
            )

        self.assertEqual(result.verdict, VerificationVerdict.RECOVERED)
        self.assertIn(("save", "verify_settle"), calls)
        self.assertIn(("sleep", 12.0), calls)
        self.assertLess(
            calls.index(("sleep", 12.0)), calls.index(("save", "verify"))
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
