from __future__ import annotations

import unittest

from fallback import FallbackStopped, run_fallback
from models import (
    ExecutionResult,
    IncidentStatus,
    RemediationAction,
    SafetyCheck,
    SafetyDecision,
    SafetyVerdict,
    VerificationResult,
    VerificationVerdict,
)
from test_safety_gate import make_incident


class Recorder:
    def __init__(self, incident):
        self.incident = incident

    def load(self, incident_id):
        if incident_id != self.incident.incident_id:
            raise KeyError(incident_id)
        return self.incident.model_copy(deep=True)

    def save(self, incident):
        self.incident = incident.model_copy(deep=True)


def failed_verification(incident):
    return VerificationResult(
        incident_id=incident.incident_id,
        action=RemediationAction.RESTART_ENCODER,
        verdict=VerificationVerdict.RECOVERY_FAILED,
        recovered=False,
        telemetry_ok=False,
        video_ok=False,
        health_value=0,
        stable_window_seconds=0,
        failed_checks=["still unhealthy"],
    )


class FallbackTests(unittest.TestCase):
    def test_one_alternate_uses_gate_control_and_verify(self):
        incident = make_incident()
        incident.actions_attempted = [RemediationAction.RESTART_ENCODER]
        incident.attempt_count = 1
        incident.execution = ExecutionResult(
            incident_id=incident.incident_id,
            action=RemediationAction.RESTART_ENCODER,
            idempotency_key="first-key",
            success=True,
            detail="called",
        )
        incident.verification = failed_verification(incident)
        recorder = Recorder(incident)
        calls = []

        def gate(incident_id):
            calls.append(("gate", recorder.incident.proposal.action))
            return SafetyDecision(
                incident_id=incident_id,
                action=RemediationAction.REDUCE_PROFILE,
                verdict=SafetyVerdict.ALLOW,
                checks=[SafetyCheck(name="all", passed=True)],
                idempotency_key="fallback-key",
            )

        def control(incident_id):
            calls.append(("control", incident_id))
            return ExecutionResult(
                incident_id=incident_id,
                action=RemediationAction.REDUCE_PROFILE,
                idempotency_key="fallback-key",
                success=True,
                detail="called",
            )

        def verify(incident_id):
            calls.append(("verify", incident_id))
            return VerificationResult(
                incident_id=incident_id,
                action=RemediationAction.REDUCE_PROFILE,
                verdict=VerificationVerdict.RECOVERED,
                recovered=True,
                telemetry_ok=True,
                video_ok=True,
                health_value=1,
                stable_window_seconds=15,
            )

        result = run_fallback(
            incident.incident_id,
            recorder=recorder,
            gate_runner=gate,
            control_runner=control,
            verify_runner=verify,
        )
        self.assertEqual(result.verdict, VerificationVerdict.RECOVERED)
        self.assertEqual(calls[0], ("gate", RemediationAction.REDUCE_PROFILE))
        self.assertEqual([name for name, _ in calls], ["gate", "control", "verify"])
        self.assertTrue(recorder.incident.fallback_attempted)
        self.assertEqual(len(recorder.incident.execution_history), 1)
        self.assertEqual(len(recorder.incident.verification_history), 1)

    def test_no_alternate_stops_without_gate_or_control(self):
        incident = make_incident()
        incident.actions_attempted = [
            RemediationAction.RESTART_ENCODER,
            RemediationAction.REDUCE_PROFILE,
        ]
        incident.attempt_count = 2
        incident.verification = failed_verification(incident)
        recorder = Recorder(incident)
        with self.assertRaises(FallbackStopped):
            run_fallback(
                incident.incident_id,
                recorder=recorder,
                gate_runner=lambda _: self.fail("gate must not run"),
                control_runner=lambda _: self.fail("control must not run"),
            )
        self.assertEqual(recorder.incident.status, IncidentStatus.AUTOMATION_FAILED)
        self.assertIn("no untried compatible", recorder.incident.automation_failure_reason)

    def test_blocked_gate_never_calls_control(self):
        incident = make_incident()
        incident.actions_attempted = [RemediationAction.RESTART_ENCODER]
        incident.attempt_count = 1
        incident.verification = failed_verification(incident)
        recorder = Recorder(incident)

        def blocked(incident_id):
            return SafetyDecision(
                incident_id=incident_id,
                action=RemediationAction.REDUCE_PROFILE,
                verdict=SafetyVerdict.BLOCK,
                checks=[SafetyCheck(name="cooldown", passed=False)],
                block_reason="cooldown: too soon",
                idempotency_key="blocked-key",
            )

        with self.assertRaises(FallbackStopped):
            run_fallback(
                incident.incident_id,
                recorder=recorder,
                gate_runner=blocked,
                control_runner=lambda _: self.fail("control must not run"),
            )
        self.assertEqual(recorder.incident.status, IncidentStatus.AUTOMATION_FAILED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
