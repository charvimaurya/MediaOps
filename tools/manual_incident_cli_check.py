"""
Manual live check for incident_cli.py -- the load / prerequisite-check /
write-back glue the per-component CLI runners share.

    python3 -m tools.manual_incident_cli_check

This intentionally lives outside unittest discovery because it uses real
Firestore. It writes to a throwaway collection (incidents_manualcli_<ts>) and
cleans that collection in finally. No Gemini / Vertex calls are made.
"""

import os
import time
from datetime import datetime, timezone
from uuid import uuid4

TS = int(time.time())
os.environ["FIRESTORE_COLLECTION"] = f"incidents_manualcli_{TS}"  # before the imports below

from incident_recorder import IncidentRecorder
from tools.incident_cli import looks_like_incident_id, run_step
from models import (
    AnomalyEvent,
    FaultClass,
    Incident,
    IncidentStatus,
    InfraFinding,
    Severity,
    VisionFinding,
    VisionSymptom,
)

UTC = timezone.utc
rec = IncidentRecorder()


def fresh_incident() -> str:
    anom = AnomalyEvent(
        fault_class=FaultClass.UNKNOWN, severity=Severity.HIGH, reason="test",
        health_value=0, telemetry_snapshot={"media_fps": 18.0}, breach_count=6,
    )
    inc = Incident(anomaly=anom, current_step="record")
    rec.save(inc)
    return inc.incident_id


def a_vision() -> VisionFinding:
    now = datetime.now(UTC)
    return VisionFinding(frame_captured_at=now, observed_at=now,
                         symptom=VisionSymptom.MACROBLOCKING, description="blocky",
                         confidence=0.9, model="fake", raw_response="{}")


def an_infra() -> InfraFinding:
    return InfraFinding(observed_at=datetime.now(UTC), fault_class=FaultClass.ENCODER_OVERLOAD,
                        affected_component="encoder_01", description="cpu high",
                        supporting_metrics={"media_fps": 18.0}, confidence=0.9,
                        model="fake", raw_response="{}")


def expect_exit(fn):
    try:
        fn()
    except SystemExit as e:
        return e
    raise AssertionError("expected SystemExit")


try:
    # ---- 1. looks_like_incident_id ------------------------------------ #
    print("1. looks_like_incident_id")
    assert looks_like_incident_id(uuid4().hex)
    assert looks_like_incident_id("4d6983a6a8c1441b803fc922d40ba688")
    for bad in ("overload", "failure", "", None, "ABCDEF", uuid4().hex + "x",
                "incidents/4d6983a6a8c1441b803fc922d40ba688"):
        assert not looks_like_incident_id(bad), bad
    print("   OK")

    # ---- 2. happy path: writes the field, advances status, persists --- #
    print("\n2. run_step happy path")
    iid = fresh_incident()
    out = run_step(iid, step_name="diagnose", status=IncidentStatus.DIAGNOSING,
                   produces="vision", compute=lambda inc: a_vision())
    assert isinstance(out, VisionFinding)
    reloaded = rec.load(iid)
    assert reloaded.vision is not None and reloaded.vision.symptom is VisionSymptom.MACROBLOCKING
    assert reloaded.status is IncidentStatus.DIAGNOSING
    assert reloaded.current_step == "diagnose"
    print("   OK: incidents/<id>.vision written, status -> DIAGNOSING, durable")

    # ---- 3. missing prerequisite -> exit, doc untouched -------------- #
    print("\n3. missing prerequisite guard")
    iid = fresh_incident()
    e = expect_exit(lambda: run_step(
        iid, step_name="aggregate", status=IncidentStatus.AGGREGATING,
        requires=("vision", "infra"), produces="evidence",
        compute=lambda inc: (_ for _ in ()).throw(AssertionError("compute must not run"))))
    assert e.code != 0
    d = rec.load(iid)
    assert d.evidence is None and d.status is IncidentStatus.DETECTED and d.notes == []
    print("   OK: exits non-zero, compute never ran, doc unchanged")

    # ---- 4. compute raises -> STOP note + exit ---------------------- #
    print("\n4. compute raises -> STOP reason persisted")
    iid = fresh_incident()
    def boom(inc):
        raise RuntimeError("evidence conflict: BLACK_FRAME vs encoder_overload")
    e = expect_exit(lambda: run_step(
        iid, step_name="aggregate", status=IncidentStatus.AGGREGATING,
        produces="evidence", compute=boom))
    assert e.code != 0
    d = rec.load(iid)
    assert d.evidence is None
    assert any("aggregate STOP" in n and "conflict" in n for n in d.notes), d.notes
    assert d.status is IncidentStatus.DETECTED  # status NOT advanced on a stop
    print(f"   OK: note = {d.notes[-1]!r}")

    # ---- 5. compute returns None -> STOP note + exit --------------- #
    print("\n5. compute returns None -> STOP")
    iid = fresh_incident()
    e = expect_exit(lambda: run_step(
        iid, step_name="decide", status=IncidentStatus.DECIDING,
        produces="proposal", compute=lambda inc: None))
    assert e.code != 0
    d = rec.load(iid)
    assert d.proposal is None and any("decide STOP" in n for n in d.notes)
    print(f"   OK: note = {d.notes[-1]!r}")

    # ---- 6. empty-list result is NOT a stop ----------------------- #
    print("\n6. empty list result (RAG: no precedent) is written, not treated as a stop")
    iid = fresh_incident()
    inc = rec.load(iid)
    inc.evidence = None  # not needed; we skip `requires` here
    out = run_step(iid, step_name="retrieve", status=IncidentStatus.RETRIEVING,
                   produces="precedent", compute=lambda inc: [],
                   render=lambda m: "no precedent" if not m else str(m))
    assert out == []
    d = rec.load(iid)
    assert d.precedent == [] and d.status is IncidentStatus.RETRIEVING
    print("   OK: precedent=[] persisted, status -> RETRIEVING")

    # ---- 7. unknown incident id -> clean error ------------------- #
    print("\n7. unknown incident id")
    e = expect_exit(lambda: run_step(
        "0" * 32, step_name="diagnose", status=IncidentStatus.DIAGNOSING,
        produces="vision", compute=lambda inc: a_vision()))
    assert e.code != 0
    print("   OK: exits with a 'no incident ... in Firestore' message")

    # ---- 8. chained steps accumulate on one doc ----------------- #
    print("\n8. vision -> infra -> (both present) on one incident")
    iid = fresh_incident()
    run_step(iid, step_name="diagnose", status=IncidentStatus.DIAGNOSING,
             produces="vision", compute=lambda inc: a_vision())
    run_step(iid, step_name="diagnose", status=IncidentStatus.DIAGNOSING,
             produces="infra", compute=lambda inc: an_infra())
    d = rec.load(iid)
    assert d.vision is not None and d.infra is not None
    print("   OK: both findings on incidents/<id>, ready for aggregator.py")

    print("\nALL CHECKS PASSED")

finally:
    n = 0
    for snap in rec._col.stream():
        snap.reference.delete()
        n += 1
    print(f"\ncleaned up {n} doc(s) from {rec._col.id}")
