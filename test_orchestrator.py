"""
Live check for orchestrator.py -- runs against real Firestore, throwaway
collection, deleted at the end.

    python3 test_orchestrator.py

Covers: happy path DETECTED->...->RESOLVED with every stub result persisted,
vision||infra concurrency, and two fail-closed paths (parallel branch + a
sequential step).
"""

import time

import orchestrator
from google.cloud import firestore
from incident_recorder import FIRESTORE_DATABASE_ID, GCP_PROJECT_ID, IncidentRecorder
from models import AnomalyEvent, FaultClass, Severity

TEST_COLLECTION = f"incidents_test_orch_{int(__import__('time').time())}"

db = firestore.Client(project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE_ID)
col = db.collection(TEST_COLLECTION)
recorder = IncidentRecorder(client=db, collection_name=TEST_COLLECTION)


def seed(reason: str) -> str:
    ev = AnomalyEvent(
        fault_class=FaultClass.UNKNOWN,
        severity=Severity.HIGH,
        reason=reason,
        health_value=0,
        telemetry_snapshot={"media_fps": 18.0},
        breach_count=6,
    )
    return recorder.record(ev)


def fresh(incident_id: str) -> dict:
    """Read straight from Firestore, not from any in-memory object."""
    return db.collection(TEST_COLLECTION).document(incident_id).get().to_dict()


def main() -> None:
    print(f"using throwaway collection: {TEST_COLLECTION}\n")

    # ---- 1. happy path -------------------------------------------------- #
    print("1. HAPPY PATH: run() drives DETECTED -> RESOLVED")
    orchestrator.VISION_INFRA_THREADS.clear()
    orchestrator.STUB_DELAY_SECONDS = 0.3  # make vision/infra genuinely overlap
    iid = seed("happy path")
    result = orchestrator.Orchestrator(recorder).run(iid)

    assert result.status.value == "RESOLVED", result.status
    d = fresh(iid)
    assert d["status"] == "RESOLVED", d["status"]
    assert d["evidence"] is not None
    assert d["proposal"] is not None
    assert d["safety_decision"] is not None
    assert d["verification"] is not None
    assert d["report_sent"] is True
    assert d["closed_at"] is not None
    assert d["actions_attempted"] == ["RESTART_ENCODER"], d["actions_attempted"]
    assert d["attempt_count"] == 1, d["attempt_count"]
    assert d["idempotency_key"].startswith("FAKE-"), d["idempotency_key"]
    assert d["evidence"]["vision"]["description"].startswith("FAKE")
    assert d["proposal"]["model"] == "fake-remediation-stub"
    print(f"   OK: Firestore doc reached RESOLVED with all stub findings attached\n")

    # ---- 2. parallel proof ------------------------------------------- #
    print("2. PARALLEL: vision + infra ran on two distinct worker threads")
    threads = list(orchestrator.VISION_INFRA_THREADS)
    assert len(threads) == 2, threads
    assert len(set(threads)) == 2, threads
    assert "MainThread" not in threads, threads

    # timing: two 0.3s stubs finish in ~0.3s (parallel), not ~0.6s (sequential)
    t0 = time.monotonic()
    orchestrator.Orchestrator(recorder)._parallel_vision_infra(None)
    elapsed = time.monotonic() - t0
    assert elapsed < 0.5, f"vision+infra took {elapsed:.2f}s -- looks sequential"
    orchestrator.STUB_DELAY_SECONDS = 0.0
    print(f"   OK: threads={threads}, parallel wall-time={elapsed:.2f}s (sequential would be ~0.6s)\n")

    # ---- 3. fail-closed in the parallel branch ---------------------- #
    print("3. FAIL-CLOSED (parallel branch): fail_at='infra'")
    iid2 = seed("fail at infra")
    raised = False
    try:
        orchestrator.Orchestrator(recorder).run(iid2, fail_at="infra")
    except Exception as exc:
        raised = True
        print(f"   run() raised: {type(exc).__name__}: {exc}")
    assert raised, "run() did not raise"
    d = fresh(iid2)
    assert d["status"] == "FAILED", d["status"]
    assert d["closed_at"] is not None
    assert any("FAILED during diagnose" in n for n in d["notes"]), d["notes"]
    assert d["evidence"] is None and d["proposal"] is None, "workflow should have stopped"
    print("   OK: incident FAILED in Firestore, later steps did not run\n")

    # ---- 4. fail-closed in a sequential step ----------------------- #
    print("4. FAIL-CLOSED (sequential step): fail_at='execute'")
    iid3 = seed("fail at execute")
    raised = False
    try:
        orchestrator.Orchestrator(recorder).run(iid3, fail_at="execute")
    except Exception as exc:
        raised = True
        print(f"   run() raised: {type(exc).__name__}: {exc}")
    assert raised, "run() did not raise"
    d = fresh(iid3)
    assert d["status"] == "FAILED", d["status"]
    assert d["evidence"] is not None and d["proposal"] is not None
    assert d["safety_decision"] is not None
    assert d["verification"] is None, "verify ran after a failed execute"
    assert d["report_sent"] is False
    assert any("FAILED during execute" in n for n in d["notes"]), d["notes"]
    print("   OK: got as far as GATING, stopped at EXECUTING, marked FAILED\n")

    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    try:
        main()
    finally:
        n = 0
        for snap in col.stream():
            snap.reference.delete()
            n += 1
        print(f"\ncleaned up {n} doc(s) from {TEST_COLLECTION}")
