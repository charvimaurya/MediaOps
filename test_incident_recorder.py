"""
Live check for incident_recorder.py -- runs against real Firestore.

    pip install google-cloud-firestore
    gcloud auth application-default login
    python3 test_incident_recorder.py

Uses a throwaway collection (incidents_test_<timestamp>) and deletes it at the
end, so it never touches the real `incidents` collection.

Covers: create, dedup/attach (no duplicate), terminal -> fresh incident,
and the fail-safe path (write error -> raises, no id returned).
"""

import time

from google.cloud import firestore

from incident_recorder import GCP_PROJECT_ID, FIRESTORE_DATABASE_ID, IncidentRecorder
from models import AnomalyEvent, FaultClass, Severity

TEST_COLLECTION = f"incidents_test_{int(time.time())}"


def make_event(reason: str) -> AnomalyEvent:
    return AnomalyEvent(
        fault_class=FaultClass.UNKNOWN,
        severity=Severity.HIGH,
        reason=reason,
        health_value=0,
        telemetry_snapshot={"media_fps": 18.0},
        breach_count=6,
    )


def count_docs(col) -> int:
    return sum(1 for _ in col.stream())


def main() -> None:
    db = firestore.Client(project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE_ID)
    col = db.collection(TEST_COLLECTION)
    recorder = IncidentRecorder(client=db, collection_name=TEST_COLLECTION)

    try:
        print(f"using throwaway collection: {TEST_COLLECTION}\n")

        # 1. create
        print("1. record() a fresh anomaly -> creates one incident")
        e1 = make_event("first observation")
        id1 = recorder.record(e1)
        doc = col.document(id1).get()
        assert doc.exists, "incident doc was not written"
        d = doc.to_dict()
        assert d["status"] == "DETECTED", d["status"]
        assert d["current_step"] == "record", d["current_step"]
        assert d["anomaly"]["event_id"] == e1.event_id
        assert d["repeated_anomalies"] == []
        assert count_docs(col) == 1
        print(f"   OK: incident {id1[:8]}  status=DETECTED  repeated_anomalies=[]")

        # 2. dedup / attach
        print("\n2. record() a second anomaly (same fault) -> attaches, no duplicate")
        e2 = make_event("second observation")
        id2 = recorder.record(e2)
        assert id2 == id1, f"expected same id, got {id2} vs {id1}"
        d = col.document(id1).get().to_dict()
        assert len(d["repeated_anomalies"]) == 1, d["repeated_anomalies"]
        assert d["repeated_anomalies"][0]["event_id"] == e2.event_id
        assert d["updated_at"] >= d["created_at"]
        assert any(e2.event_id in n for n in d["notes"]), d["notes"]
        assert count_docs(col) == 1, "a duplicate incident was created"
        print(f"   OK: same id {id2[:8]}, 1 repeated anomaly, 1 doc total")
        print(f"   note: {d['notes'][-1]}")

        # 3. terminal -> fresh incident
        print("\n3. mark incident terminal (RECOVERED) -> next anomaly starts a new incident")
        col.document(id1).update({"status": "RECOVERED"})
        e3 = make_event("after recovery, a new problem")
        id3 = recorder.record(e3)
        assert id3 != id1, "should not attach to a terminal incident"
        assert count_docs(col) == 2
        print(f"   OK: new incident {id3[:8]}, 2 docs total")

        # 4. fail-safe: a write error must raise, not return an id
        print("\n4. Firestore write fails -> record() raises, returns no id")
        raised = False
        try:
            _BrokenRecorder().record(make_event("during an outage"))
        except Exception as exc:  # noqa: BLE001 - we want any failure to surface
            raised = True
            print(f"   OK: raised {type(exc).__name__}: {exc}")
        assert raised, "record() returned instead of raising on a write failure"

        print("\nALL CHECKS PASSED")

    finally:
        deleted = 0
        for snap in col.stream():
            snap.reference.delete()
            deleted += 1
        print(f"\ncleaned up {deleted} doc(s) from {TEST_COLLECTION}")


# -- stub client whose write always fails (for check 4) --------------------- #

class _RaisingDoc:
    def set(self, *a, **k):
        raise RuntimeError("simulated Firestore outage")

    def get(self):  # pragma: no cover - should never be reached
        raise AssertionError("read-back should not run after a failed write")


class _RaisingCollection:
    def where(self, *a, **k):
        return self

    def stream(self):
        return iter(())  # no active incidents

    def document(self, _id):
        return _RaisingDoc()


class _RaisingClient:
    def collection(self, _name):
        return _RaisingCollection()


def _BrokenRecorder() -> IncidentRecorder:
    return IncidentRecorder(client=_RaisingClient(), collection_name=TEST_COLLECTION)


if __name__ == "__main__":
    main()
