from __future__ import annotations

import unittest

from kb_writeback import KBWritebackError, run_writeback
from models import ExecutionResult, VerificationResult, VerificationVerdict
from tests.test_safety_gate import make_incident


class Recorder:
    def __init__(self, incident):
        self.incident = incident

    def load(self, incident_id):
        return self.incident.model_copy(deep=True)

    def save(self, incident):
        self.incident = incident.model_copy(deep=True)


def prepared_incident(verdict=VerificationVerdict.RECOVERED):
    incident = make_incident()
    action = incident.proposal.action
    incident.execution = ExecutionResult(
        incident_id=incident.incident_id,
        action=action,
        idempotency_key="key",
        success=True,
        detail="called",
    )
    incident.verification = VerificationResult(
        incident_id=incident.incident_id,
        action=action,
        verdict=verdict,
        recovered=verdict is VerificationVerdict.RECOVERED,
        telemetry_ok=verdict is VerificationVerdict.RECOVERED,
        video_ok=verdict is VerificationVerdict.RECOVERED,
        health_value=1 if verdict is VerificationVerdict.RECOVERED else 0,
        stable_window_seconds=15,
        failed_checks=[] if verdict is VerificationVerdict.RECOVERED else ["bad"],
    )
    return incident


class KBWritebackTests(unittest.TestCase):
    def test_recovered_incident_writes_successful_precedent(self):
        incident = prepared_incident()
        recorder = Recorder(incident)
        written = {}

        def writer(_, record):
            written.update(record)
            return record, True

        result = run_writeback(
            incident.incident_id,
            recorder=recorder,
            embedder=lambda text, *, task_type: [0.1, 0.2, 0.3],
            writer=writer,
        )
        self.assertTrue(result["created"])
        self.assertEqual(written["outcome"], "resolved")
        self.assertEqual(written["action_taken"], incident.execution.action.value)
        self.assertEqual(written["embedding_task_type"], "RETRIEVAL_DOCUMENT")
        self.assertNotIn("evidence_summary", written)
        self.assertNotIn("stable_window_seconds", written["verification_summary"])
        self.assertNotIn("confidence", written["summary"].lower())
        self.assertNotIn("conf ", written["summary"].lower())
        self.assertEqual(recorder.incident.kb_writeback_id, written["kb_id"])

    def test_failed_verification_never_embeds_or_writes(self):
        incident = prepared_incident(VerificationVerdict.RECOVERY_FAILED)
        recorder = Recorder(incident)
        with self.assertRaises(KBWritebackError):
            run_writeback(
                incident.incident_id,
                recorder=recorder,
                embedder=lambda *_args, **_kwargs: self.fail("must not embed"),
                writer=lambda *_: self.fail("must not write"),
            )

    def test_existing_incident_precedent_is_idempotent(self):
        incident = prepared_incident()
        recorder = Recorder(incident)

        def existing(_, record):
            return record, False

        result = run_writeback(
            incident.incident_id,
            recorder=recorder,
            embedder=lambda text, *, task_type: [1.0],
            writer=existing,
        )
        self.assertFalse(result["created"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
