"""Unit tests for the deterministic, idempotent Control Plane."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import control_plane
from models import IncidentStatus, RemediationAction, SafetyVerdict
from safety_gate import evaluate_safety
from tests.test_safety_gate import CONFIG, NOW, make_incident


class FakeSnapshot:
    def __init__(self, document_id, data):
        self.id = document_id
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeReference:
    def __init__(self, collection, document_id):
        self.collection = collection
        self.document_id = document_id

    def get(self, transaction=None):
        return FakeSnapshot(self.document_id, self.collection.documents.get(self.document_id))


class FakeCollection:
    def __init__(self, name, documents=None):
        self.id = name
        self.documents = documents or {}

    def document(self, document_id):
        return FakeReference(self, document_id)


class FakeTransaction:
    def set(self, ref, data):
        ref.collection.documents[ref.document_id] = data

    def update(self, ref, fields):
        ref.collection.documents[ref.document_id].update(fields)


class FakeDB:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        return self.collections.setdefault(name, FakeCollection(name))

    def transaction(self):
        return FakeTransaction()


class FakeRecorder:
    def __init__(self, incident):
        self._db = FakeDB()
        self._col = self._db.collection("incidents")
        self._col.documents[incident.incident_id] = incident.model_dump(mode="json")


class RecordingController:
    def __init__(self):
        self.calls = []

    def restart_encoder(self):
        self.calls.append(("restart_encoder",))
        return True, "encoder restarted"

    def reduce_bitrate(self, factor):
        self.calls.append(("reduce_bitrate", factor))
        return True, f"bitrate {factor}"

    def switch_backup(self):
        self.calls.append(("switch_backup",))
        return True, "backup switched"

    def failover(self):
        self.calls.append(("failover",))
        return True, "failed over"


class FakeHttpResponse:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def allowed_incident(action=RemediationAction.RESTART_ENCODER):
    inc = make_incident(action=action)
    decision = evaluate_safety(inc, config=CONFIG, now=NOW)
    if action is RemediationAction.FAILOVER:
        # Used only to exercise the closed dispatch mapping; construct the same
        # gate-authorized shape without changing Safety Gate compatibility.
        decision.verdict = SafetyVerdict.ALLOW
        decision.checks[-1].passed = True
        decision.checks[-1].detail = "test authorization"
        decision.block_reason = None
    inc.safety_decision = decision
    inc.idempotency_key = decision.idempotency_key
    return inc


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.transactional = patch.object(
            control_plane.firestore, "transactional", side_effect=lambda fn: fn
        )
        self.transactional.start()

    def tearDown(self):
        self.transactional.stop()

    def test_invoke_has_exact_closed_mapping(self):
        controller = RecordingController()
        expected = [
            (RemediationAction.RESTART_ENCODER, ("restart_encoder",)),
            (RemediationAction.REDUCE_PROFILE, ("reduce_bitrate", 0.5)),
            (RemediationAction.SWITCH_SOURCE, ("switch_backup",)),
            (RemediationAction.FAILOVER, ("failover",)),
        ]
        for action, call in expected:
            control_plane._invoke(controller, action)
            self.assertEqual(controller.calls[-1], call)

    def test_http_client_calls_only_fixed_post_paths(self):
        calls = []

        def opener(request, timeout):
            calls.append((request.full_url, request.method, timeout))
            action = {
                "/control/restart-encoder": "RESTART_ENCODER",
                "/control/reduce-profile": "REDUCE_PROFILE",
                "/control/switch-source": "SWITCH_SOURCE",
                "/control/failover": "FAILOVER",
            }[request.full_url.removeprefix("http://sim")]
            return FakeHttpResponse({"action": action, "ok": True, "detail": "done"})

        client = control_plane.SimulatorControlClient(
            "http://sim", timeout_seconds=4, opener=opener
        )
        control_plane._invoke(client, RemediationAction.RESTART_ENCODER)
        control_plane._invoke(client, RemediationAction.REDUCE_PROFILE)
        control_plane._invoke(client, RemediationAction.SWITCH_SOURCE)
        control_plane._invoke(client, RemediationAction.FAILOVER)
        self.assertEqual([call[0] for call in calls], [
            "http://sim/control/restart-encoder",
            "http://sim/control/reduce-profile",
            "http://sim/control/switch-source",
            "http://sim/control/failover",
        ])
        self.assertTrue(all(method == "POST" and timeout == 4 for _, method, timeout in calls))

    def test_http_client_rejects_malformed_or_mismatched_response(self):
        for payload in (
            {"action": "FAILOVER", "ok": True, "detail": "wrong action"},
            {"action": "RESTART_ENCODER", "ok": "yes", "detail": "bad bool"},
            {"action": "RESTART_ENCODER", "ok": True},
        ):
            with self.subTest(payload=payload):
                client = control_plane.SimulatorControlClient(
                    "http://sim",
                    opener=lambda request, timeout, value=payload: FakeHttpResponse(value),
                )
                with self.assertRaises(ValueError):
                    client.restart_encoder()

    def test_allow_executes_and_records_without_recovery(self):
        inc = allowed_incident()
        recorder = FakeRecorder(inc)
        controller = RecordingController()
        result = control_plane.execute_incident(
            inc.incident_id, recorder=recorder, controller=controller
        )

        self.assertTrue(result.success)
        self.assertEqual(controller.calls, [("restart_encoder",)])
        stored = recorder._col.documents[inc.incident_id]
        self.assertEqual(stored["status"], IncidentStatus.EXECUTING.value)
        self.assertEqual(stored["execution"]["action"], "RESTART_ENCODER")
        self.assertTrue(stored["execution"]["success"])
        self.assertIsNone(stored["verification"])
        self.assertEqual(stored["attempt_count"], 1)

    def test_missing_or_block_decision_never_executes(self):
        for kind in ("missing", "block"):
            with self.subTest(kind=kind):
                inc = allowed_incident()
                if kind == "missing":
                    inc.safety_decision = None
                else:
                    inc.safety_decision.verdict = SafetyVerdict.BLOCK
                    inc.safety_decision.block_reason = "test block"
                recorder = FakeRecorder(inc)
                controller = RecordingController()
                with self.assertRaises(control_plane.ControlPlaneBlocked):
                    control_plane.execute_incident(
                        inc.incident_id, recorder=recorder, controller=controller
                    )
                self.assertEqual(controller.calls, [])

    def test_completed_duplicate_returns_prior_result_without_execution(self):
        inc = allowed_incident()
        recorder = FakeRecorder(inc)
        first_controller = RecordingController()
        first = control_plane.execute_incident(
            inc.incident_id, recorder=recorder, controller=first_controller
        )
        duplicate_controller = RecordingController()
        second = control_plane.execute_incident(
            inc.incident_id, recorder=recorder, controller=duplicate_controller
        )
        self.assertEqual(second, first)
        self.assertEqual(first_controller.calls, [("restart_encoder",)])
        self.assertEqual(duplicate_controller.calls, [])
        self.assertEqual(recorder._col.documents[inc.incident_id]["attempt_count"], 1)

    def test_in_progress_claim_refuses_duplicate(self):
        inc = allowed_incident()
        recorder = FakeRecorder(inc)
        key = inc.idempotency_key
        ledger_id = control_plane._ledger_document_id(key)
        recorder._db.collection(control_plane.CONTROL_EXECUTIONS_COLLECTION).documents[ledger_id] = {
            "state": "IN_PROGRESS", "idempotency_key": key,
        }
        controller = RecordingController()
        with self.assertRaises(control_plane.ExecutionInProgress):
            control_plane.execute_incident(
                inc.incident_id, recorder=recorder, controller=controller
            )
        self.assertEqual(controller.calls, [])

    def test_control_exception_is_recorded_as_failure(self):
        inc = allowed_incident()
        recorder = FakeRecorder(inc)
        controller = RecordingController()
        controller.restart_encoder = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        result = control_plane.execute_incident(
            inc.incident_id, recorder=recorder, controller=controller
        )
        self.assertFalse(result.success)
        self.assertEqual(result.detail, "RuntimeError: boom")
        stored = recorder._col.documents[inc.incident_id]
        self.assertFalse(stored["execution"]["success"])
        self.assertEqual(stored["status"], IncidentStatus.EXECUTING.value)


if __name__ == "__main__":
    unittest.main(verbosity=2)
