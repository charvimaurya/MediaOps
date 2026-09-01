"""Deterministic unit tests for the Step 10 Safety Gate."""

from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import safety_gate
from models import (
    AnomalyEvent,
    FaultClass,
    Incident,
    IncidentEvidence,
    InfraFinding,
    RemediationAction,
    RemediationProposal,
    SafetyVerdict,
    Severity,
    VisionFinding,
    VisionSymptom,
)
from safety_gate import BlastRadius, SafetyConfig, evaluate_safety


NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
CONFIG = SafetyConfig(0.8, 2, 60.0, BlastRadius.STREAM)


def make_incident(
    *,
    fault: FaultClass = FaultClass.ENCODER_OVERLOAD,
    action: RemediationAction = RemediationAction.RESTART_ENCODER,
    target: str = "encoder_01",
    evidence_confidence: float = 0.9,
    proposal_confidence: float = 0.9,
) -> Incident:
    vision = VisionFinding(
        observed_at=NOW,
        frame_captured_at=NOW,
        symptom=VisionSymptom.MACROBLOCKING,
        description="blocky video",
        confidence=0.9,
        model="test",
        raw_response="{}",
    )
    infra = InfraFinding(
        observed_at=NOW,
        fault_class=fault,
        affected_component=target,
        description="test telemetry",
        supporting_metrics={"media_pipeline_health": 0.0},
        confidence=0.9,
        model="test",
        raw_response="{}",
    )
    anomaly = AnomalyEvent(
        detected_at=NOW,
        fault_class=fault,
        severity=Severity.HIGH,
        reason="test anomaly",
        health_value=0,
        telemetry_snapshot={"media_pipeline_health": 0.0},
        breach_count=3,
    )
    incident = Incident(anomaly=anomaly, created_at=NOW, updated_at=NOW)
    incident.evidence = IncidentEvidence(
        incident_id=incident.incident_id,
        aggregated_at=NOW,
        vision=vision,
        infra=infra,
        agreement=True,
        fault_class=fault,
        confidence=evidence_confidence,
        summary="validated test evidence",
        validation_passed=True,
        validation_errors=[],
    )
    incident.proposal = RemediationProposal(
        incident_id=incident.incident_id,
        proposed_at=NOW,
        action=action,
        rationale="test proposal",
        confidence=proposal_confidence,
        model="test",
    )
    return incident


class SafetyGateTests(unittest.TestCase):
    def assert_blocked(self, incident: Incident, check_name: str) -> None:
        result = evaluate_safety(incident, config=CONFIG, now=NOW)
        self.assertEqual(result.verdict, SafetyVerdict.BLOCK)
        self.assertIsNotNone(result.block_reason)
        self.assertEqual(result.checks[-1].name, check_name)
        self.assertFalse(result.checks[-1].passed)

    def test_all_checks_must_pass_to_allow(self) -> None:
        result = evaluate_safety(make_incident(), config=CONFIG, now=NOW)
        self.assertEqual(result.verdict, SafetyVerdict.ALLOW)
        self.assertIsNone(result.block_reason)
        self.assertEqual(
            [check.name for check in result.checks],
            [
                "allow_list", "incident_match", "compatibility", "approved_target",
                "evidence_confidence", "blast_radius", "action_budget", "cooldown",
            ],
        )
        self.assertTrue(all(check.passed for check in result.checks))

    def test_compatibility_mapping(self) -> None:
        allowed = {
            FaultClass.ENCODER_OVERLOAD: {
                RemediationAction.RESTART_ENCODER, RemediationAction.REDUCE_PROFILE,
            },
            FaultClass.NETWORK_DEGRADATION: {RemediationAction.SWITCH_SOURCE},
            FaultClass.ENCODER_FAILURE: {
                RemediationAction.RESTART_ENCODER, RemediationAction.SWITCH_SOURCE,
                RemediationAction.FAILOVER,
            },
            FaultClass.UNKNOWN: set(),
        }
        targets = {
            FaultClass.ENCODER_OVERLOAD: "encoder_01",
            FaultClass.NETWORK_DEGRADATION: "network path",
            FaultClass.ENCODER_FAILURE: "encoder_01",
            FaultClass.UNKNOWN: "n/a",
        }
        for fault in FaultClass:
            for action in RemediationAction:
                with self.subTest(fault=fault, action=action):
                    result = evaluate_safety(
                        make_incident(fault=fault, action=action, target=targets[fault]),
                        config=CONFIG,
                        now=NOW,
                    )
                    expected = action in allowed[fault]
                    self.assertEqual(result.verdict is SafetyVerdict.ALLOW, expected)

    def test_mismatched_incident_id_blocks(self) -> None:
        incident = make_incident()
        incident.proposal.incident_id = "different"
        self.assert_blocked(incident, "incident_match")

    def test_unapproved_target_blocks(self) -> None:
        self.assert_blocked(make_incident(target="database_01"), "approved_target")

    def test_invalid_evidence_blocks(self) -> None:
        incident = make_incident()
        incident.evidence.validation_passed = False
        self.assert_blocked(incident, "evidence_confidence")

    def test_low_evidence_confidence_blocks(self) -> None:
        self.assert_blocked(make_incident(evidence_confidence=0.79), "evidence_confidence")

    def test_low_proposal_confidence_blocks(self) -> None:
        self.assert_blocked(make_incident(proposal_confidence=0.79), "evidence_confidence")

    def test_blast_radius_blocks(self) -> None:
        config = SafetyConfig(0.8, 2, 60.0, BlastRadius.COMPONENT)
        result = evaluate_safety(
            make_incident(action=RemediationAction.REDUCE_PROFILE), config=config, now=NOW
        )
        self.assertEqual(result.verdict, SafetyVerdict.BLOCK)
        self.assertEqual(result.checks[-1].name, "blast_radius")

    def test_exhausted_or_inconsistent_budget_blocks(self) -> None:
        exhausted = make_incident()
        exhausted.actions_attempted = [
            RemediationAction.RESTART_ENCODER, RemediationAction.REDUCE_PROFILE,
        ]
        exhausted.attempt_count = 2
        self.assert_blocked(exhausted, "action_budget")

        inconsistent = make_incident()
        inconsistent.attempt_count = 1
        self.assert_blocked(inconsistent, "action_budget")

    def test_cooldown_blocks_then_passes(self) -> None:
        incident = make_incident()
        incident.actions_attempted = [RemediationAction.RESTART_ENCODER]
        incident.attempt_count = 1
        incident.updated_at = NOW - timedelta(seconds=30)
        self.assert_blocked(incident, "cooldown")

        incident.updated_at = NOW - timedelta(seconds=60)
        result = evaluate_safety(incident, config=CONFIG, now=NOW)
        self.assertEqual(result.verdict, SafetyVerdict.ALLOW)

    def test_missing_proposal_and_evidence_block(self) -> None:
        no_proposal = make_incident()
        no_proposal.proposal = None
        self.assert_blocked(no_proposal, "proposal")

        no_evidence = make_incident()
        no_evidence.evidence = None
        self.assert_blocked(no_evidence, "evidence_confidence")

    def test_invalid_environment_config_blocks(self) -> None:
        with patch.dict(os.environ, {"SAFETY_MIN_CONFIDENCE": "not-a-number"}, clear=False):
            result = evaluate_safety(make_incident(), now=NOW)
        self.assertEqual(result.verdict, SafetyVerdict.BLOCK)
        self.assertEqual(result.checks[-1].name, "gate_error")

    def test_unexpected_check_exception_blocks(self) -> None:
        with patch.object(safety_gate, "_check_blast_radius", side_effect=RuntimeError("boom")):
            result = evaluate_safety(make_incident(), config=CONFIG, now=NOW)
        self.assertEqual(result.verdict, SafetyVerdict.BLOCK)
        self.assertEqual(result.checks[-1].name, "check_error")
        self.assertIn("boom", result.block_reason)

    def test_idempotency_key_is_stable_per_pending_attempt(self) -> None:
        incident = make_incident()
        first = evaluate_safety(incident, config=CONFIG, now=NOW)
        second = evaluate_safety(incident, config=CONFIG, now=NOW)
        self.assertEqual(first.idempotency_key, second.idempotency_key)

        incident.actions_attempted = [RemediationAction.RESTART_ENCODER]
        incident.attempt_count = 1
        incident.updated_at = NOW - timedelta(seconds=60)
        third = evaluate_safety(incident, config=CONFIG, now=NOW)
        self.assertNotEqual(first.idempotency_key, third.idempotency_key)

    def test_no_ai_modules_are_imported(self) -> None:
        self.assertNotIn("google.adk", sys.modules)
        self.assertNotIn("google.genai", sys.modules)

    def test_standalone_runner_persists_gating_and_decision(self) -> None:
        incident = make_incident()

        class RecordingRecorder:
            instance = None

            def __init__(self) -> None:
                self.saved = []
                RecordingRecorder.instance = self

            def load(self, incident_id: str) -> Incident:
                self.asserted_id = incident_id
                return incident

            def save(self, value: Incident) -> None:
                self.saved.append(value.model_copy(deep=True))

        with patch.object(safety_gate, "IncidentRecorder", RecordingRecorder), patch.object(
            safety_gate.SafetyConfig, "from_env", return_value=CONFIG
        ):
            decision = safety_gate.run_for_incident(incident.incident_id)

        recorder = RecordingRecorder.instance
        self.assertEqual(recorder.asserted_id, incident.incident_id)
        self.assertEqual(len(recorder.saved), 2)
        self.assertEqual(recorder.saved[0].status, safety_gate.IncidentStatus.GATING)
        self.assertEqual(recorder.saved[0].current_step, "gate")
        self.assertEqual(recorder.saved[1].safety_decision, decision)
        self.assertEqual(recorder.saved[1].idempotency_key, decision.idempotency_key)


if __name__ == "__main__":
    unittest.main(verbosity=2)
