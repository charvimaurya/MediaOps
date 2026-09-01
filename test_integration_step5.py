"""
End-to-end integration test for Steps 1-7.

Runs the REAL components as one chain against the live simulator, live Firestore,
Grafana MCP, and real Gemini calls:

    simulator fault -> Detector -> AnomalyEvent -> Incident Recorder (Firestore)
                    -> Orchestrator
                         -> REAL Vision Agent (Gemini)  ||  REAL Infra Agent (Gemini via Grafana MCP)
                         -> REAL Aggregator (deterministic gate)
                         -> ... (fake retrieve/decide/gate/execute/verify/report)
                         -> RESOLVED

Vision and Infra are spliced into the orchestrator flow by monkey-patch here; the
Aggregator is wired into orchestrator.py for real. Steps after aggregate are still
fake stubs -- expected; this proves the plumbing + the real diagnosticians + the
evidence gate.

    python3 test_integration_step5.py

Prereqs: simulator + Prometheus + Grafana running, simulator/output/messy_video.mov
present, ffmpeg + uvx on PATH, .env with GRAFANA_SERVICE_ACCOUNT_TOKEN, ADC + Vertex.
"""

import json
import logging
import threading
import time
import urllib.request

from google.cloud import firestore

import infra_agent
import orchestrator
import vision_agent
from detector import PROMETHEUS_URL, Detector, query_health
from incident_recorder import FIRESTORE_DATABASE_ID, GCP_PROJECT_ID, IncidentRecorder
from models import FaultClass, IncidentStatus, InfraFinding, VisionFinding, VisionSymptom

# --- config -------------------------------------------------------------- #
SIM = "http://localhost:8001"
PERSISTENCE_WINDOW = 8.0          # < the 15s default, for test speed; same mechanism
POLL_INTERVAL = 2.0
DETECT_TIMEOUT = 120
TEST_COLLECTION = f"incidents_integration_{int(time.time())}"

for name in ("detector", "vision_agent", "infra_agent", "incident_recorder", "orchestrator"):
    logging.getLogger(name).setLevel(logging.INFO)
logging.basicConfig(level=logging.WARNING, format="   %(name)s | %(message)s")


def post(path: str) -> dict:
    req = urllib.request.Request(SIM + path, method="POST")
    with urllib.request.urlopen(req, timeout=5) as r:
        return json.loads(r.read().decode())


def banner(n, label):
    print(f"\n{'='*74}\nSTAGE {n} -- {label}\n{'='*74}")


class RecordingRecorder(IncidentRecorder):
    """Captures the status Firestore holds after every orchestrator save()."""

    def __init__(self, db):
        super().__init__(client=db, collection_name=TEST_COLLECTION)
        self._db = db
        self.timeline: list[str] = []

    def save(self, incident):
        super().save(incident)
        doc = self._db.collection(TEST_COLLECTION).document(incident.incident_id).get()
        self.timeline.append(doc.to_dict()["status"])


def main() -> None:
    db = firestore.Client(project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE_ID)
    recorder = RecordingRecorder(db)

    # ---- STAGE 0 -------------------------------------------------------- #
    banner(0, "simulator is healthy")
    h = query_health(PROMETHEUS_URL)
    if h != 1:
        print(f"   health={h}, resetting and waiting...")
        post("/recovery/reset")
        time.sleep(12)
        h = query_health(PROMETHEUS_URL)
    assert h == 1, f"simulator not healthy (media_pipeline_health={h})"
    print(f"   OK: media_pipeline_health == {h}")

    # ---- STAGE 1 ------------------------------------------------------ #
    banner(1, "inject a real fault (POST /failure/encoder-overload)")
    resp = post("/failure/encoder-overload")
    assert resp.get("failure") == "encoder_overload", resp
    print(f"   OK: {resp}")
    print("   Infra will read this from live telemetry; Vision reads the 5-11s")
    print("   'overload' (blocky) section of the messy video.")

    # ---- STAGE 2 ---------------------------------------------------- #
    banner(2, "Detector picks it up and emits exactly one AnomalyEvent")
    print(f"   (persistence window {PERSISTENCE_WINDOW:.0f}s, poll {POLL_INTERVAL:.0f}s -- "
          f"default window is 15s)")
    events: list = []
    det = Detector(
        events.append,
        prometheus_url=PROMETHEUS_URL,
        persistence_window_seconds=PERSISTENCE_WINDOW,
        poll_interval_seconds=POLL_INTERVAL,
    )
    deadline = time.monotonic() + DETECT_TIMEOUT
    while not events and time.monotonic() < deadline:
        det.poll_once()
        time.sleep(POLL_INTERVAL)
    assert len(events) == 1, f"expected 1 AnomalyEvent, got {len(events)}"

    for _ in range(6):  # keep polling -- dedup latch must hold
        det.poll_once()
        time.sleep(POLL_INTERVAL)
    assert len(events) == 1, f"dedup failed: {len(events)} events"

    anomaly = events[0]
    print(f"\n   OK: exactly 1 AnomalyEvent (still 1 after ~12s more polling)")
    print("   " + anomaly.model_dump_json(indent=2).replace("\n", "\n   "))

    # ---- STAGE 3 ------------------------------------------------- #
    banner(3, "Incident Recorder -> one durable Firestore incident")
    incident_id = recorder.record(anomaly)
    recorder.timeline.append("DETECTED")  # the recorder's create, before the orchestrator
    doc = db.collection(TEST_COLLECTION).document(incident_id).get()
    assert doc.exists and doc.to_dict()["status"] == "DETECTED", doc.to_dict()
    assert sum(1 for _ in db.collection(TEST_COLLECTION).stream()) == 1
    print(f"   OK: incident_id = {incident_id}  (status DETECTED, 1 doc in collection)")
    print(f"   console: https://console.cloud.google.com/firestore/databases/-default-/data/"
          f"panel/{TEST_COLLECTION}/{incident_id}?project={GCP_PROJECT_ID}")

    # ---- STAGE 4 ---------------------------------------------- #
    banner(4, "Orchestrator: REAL Vision || REAL Infra, then the REAL Aggregator")
    _orig_vision, _orig_infra = orchestrator.fake_vision, orchestrator.fake_infra
    agent_threads: list[tuple[str, str]] = []

    def real_vision(fail: bool = False) -> VisionFinding:
        agent_threads.append(("vision", threading.current_thread().name))
        f = vision_agent.analyze_frame(fault="overload")  # real ADK -> Vertex Gemini
        if f is None:
            raise RuntimeError("vision agent returned None -> diagnostic should stop")
        return f

    def real_infra(fail: bool = False) -> InfraFinding:
        agent_threads.append(("infra", threading.current_thread().name))
        f = infra_agent.analyze_infra()  # real Grafana MCP read + Vertex Gemini
        if f is None:
            raise RuntimeError("infra agent returned None -> diagnostic should stop")
        return f

    orchestrator.fake_vision = real_vision
    orchestrator.fake_infra = real_infra
    try:
        t0 = time.monotonic()
        final = orchestrator.Orchestrator(recorder).run(incident_id)
        run_seconds = time.monotonic() - t0
    finally:
        orchestrator.fake_vision, orchestrator.fake_infra = _orig_vision, _orig_infra

    vf = final.evidence.vision
    inf = final.evidence.infra

    assert isinstance(vf, VisionFinding) and vf.model == vision_agent.VISION_MODEL, vf
    assert isinstance(inf, InfraFinding) and inf.model == infra_agent.INFRA_MODEL, inf
    assert vf.source == "vision" and inf.source == "infra"
    assert inf.supporting_metrics, "infra finding carries no metrics"

    print("\n   >>> REAL Vision Agent finding:")
    print("   " + vf.model_dump_json(indent=2).replace("\n", "\n   "))
    print("\n   >>> REAL Infra Agent finding:")
    print("   " + inf.model_dump_json(indent=2).replace("\n", "\n   "))

    # both ran concurrently on the orchestrator's thread pool
    names = {who: name for who, name in agent_threads}
    assert len(agent_threads) == 2 and len(set(names.values())) == 2
    assert "MainThread" not in names.values(), names
    print(f"\n   PARALLEL: vision on {names['vision']}, infra on {names['infra']} "
          f"(distinct worker threads); DIAGNOSING..RESOLVED wall time {run_seconds:.1f}s")

    # the REAL Aggregator ran inside the flow -- validate + agreement-check + package
    ev = final.evidence
    assert ev.validation_passed is True, ev.validation_errors
    assert ev.agreement is True, "aggregator did not find vision/infra in agreement"
    assert ev.fault_class is FaultClass.ENCODER_OVERLOAD, ev.fault_class
    assert not ev.summary.startswith("FAKE"), "fake_aggregate ran, not the real Aggregator"
    assert ev.confidence == min(vf.confidence, inf.confidence), ev.confidence
    print("\n   >>> REAL Aggregator output (IncidentEvidence):")
    print("   " + ev.model_dump_json(indent=2).replace("\n", "\n   "))
    print(f"\n   AGREEMENT: vision {vf.symptom.value} + infra {inf.fault_class.value} "
          f"-> agreed fault_class {ev.fault_class.value}, confidence {ev.confidence}, "
          f"validation_passed {ev.validation_passed}")

    # ---- STAGE 5 ------------------------------------------- #
    banner(5, "incident status advanced all the way to RESOLVED in Firestore")
    assert final.status is IncidentStatus.RESOLVED, final.status
    d = db.collection(TEST_COLLECTION).document(incident_id).get().to_dict()
    assert d["status"] == "RESOLVED", d["status"]
    assert d["closed_at"] is not None
    assert d["evidence"] and d["proposal"] and d["safety_decision"] and d["verification"]
    assert d["report_sent"] is True
    assert d["evidence"]["vision"]["model"] == vision_agent.VISION_MODEL
    assert d["evidence"]["infra"]["model"] == infra_agent.INFRA_MODEL
    assert d["evidence"]["infra"]["fault_class"] == "encoder_overload"
    assert d["evidence"]["agreement"] is True
    assert d["evidence"]["fault_class"] == "encoder_overload"
    assert d["evidence"]["validation_passed"] is True
    assert not d["evidence"]["summary"].startswith("FAKE")
    print("   OK: Firestore doc is RESOLVED; real findings + real aggregated evidence persisted")
    print("\n   status as persisted in Firestore, write by write:")
    for i, s in enumerate(recorder.timeline, 1):
        print(f"      {i:2}. {s}")


if __name__ == "__main__":
    ok = False
    try:
        main()
        ok = True
    finally:
        banner(6, "reset simulator to healthy")
        try:
            print(f"   {post('/recovery/reset')}")
        except Exception as exc:  # noqa: BLE001
            print(f"   reset failed: {exc}")
        if ok:
            print("\nALL STAGES PASSED")
            print(f"\nthrowaway collection left for inspection: {TEST_COLLECTION}")
            print("delete it with:")
            print(f"  python3 -c \"from google.cloud import firestore; "
                  f"[d.reference.delete() for d in firestore.Client(project='{GCP_PROJECT_ID}',"
                  f"database='{FIRESTORE_DATABASE_ID}').collection('{TEST_COLLECTION}').stream()]\"")
