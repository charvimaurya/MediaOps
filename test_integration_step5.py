"""
End-to-end integration test for Steps 1-5.

Runs the REAL components as one chain against the live simulator, live Firestore,
and a real Gemini call:

    simulator fault -> Detector -> AnomalyEvent -> Incident Recorder (Firestore)
                    -> Orchestrator -> REAL Vision Agent (Gemini) -> ... -> RESOLVED

The Vision Agent is spliced into the orchestrator flow by a monkey-patch here
(real wiring is Step 6). Every other orchestrator step is still a fake stub --
that is expected; this proves the plumbing, not the remaining logic.

    python3 test_integration_step5.py

Prereqs: simulator running (uvicorn simulator.control_api:app --port 8001),
ffmpeg on PATH, ADC configured, Vertex AI enabled.
"""

import json
import logging
import time
import urllib.request
from datetime import datetime, timezone

from google.cloud import firestore

import orchestrator
import vision_agent
from detector import PROMETHEUS_URL, Detector, query_health
from incident_recorder import FIRESTORE_DATABASE_ID, GCP_PROJECT_ID, IncidentRecorder
from models import IncidentStatus, VisionFinding

# --- config -------------------------------------------------------------- #
SIM = "http://localhost:8001"
PERSISTENCE_WINDOW = 8.0          # < the 15s default, for test speed; same mechanism
POLL_INTERVAL = 2.0
DETECT_TIMEOUT = 120
TEST_COLLECTION = f"incidents_integration_{int(time.time())}"

for name in ("detector", "vision_agent", "incident_recorder", "orchestrator"):
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
    banner(1, "inject a real fault (POST /failure/encoder-crash)")
    resp = post("/failure/encoder-crash")
    assert resp.get("failure") == "encoder_failure", resp
    print(f"   OK: {resp}")
    print("   note: this fault is telemetry-only in this sim -- the primary video")
    print("   (a colour-bars card) doesn't change, so Vision will honestly say COLOR_BARS.")

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
    banner(4, "Orchestrator drives the incident -- REAL Vision Agent runs")
    _orig_vision = orchestrator.fake_vision

    def real_vision(fail: bool = False) -> VisionFinding:
        finding = vision_agent.analyze_frame()  # real ADK -> Vertex Gemini call
        if finding is None:
            raise RuntimeError("vision agent returned None -> diagnostic should stop")
        return finding

    orchestrator.fake_vision = real_vision
    try:
        final = orchestrator.Orchestrator(recorder).run(incident_id)
    finally:
        orchestrator.fake_vision = _orig_vision

    vf = final.evidence.vision
    assert isinstance(vf, VisionFinding), type(vf)
    assert vf.model == vision_agent.VISION_MODEL, f"stub ran, not the real agent: {vf.model}"
    assert vf.source == "vision"
    assert 0.0 <= vf.confidence <= 1.0
    assert vf.raw_response and json.loads(vf.raw_response)
    print("\n   >>> ACTUAL Vision Agent finding (real Gemini call, inside the orchestrated flow):")
    print("   " + vf.model_dump_json(indent=2).replace("\n", "\n   "))

    # ---- STAGE 5 ------------------------------------------- #
    banner(5, "incident status advanced all the way to RESOLVED in Firestore")
    assert final.status is IncidentStatus.RESOLVED, final.status
    d = db.collection(TEST_COLLECTION).document(incident_id).get().to_dict()
    assert d["status"] == "RESOLVED", d["status"]
    assert d["closed_at"] is not None
    assert d["evidence"] and d["proposal"] and d["safety_decision"] and d["verification"]
    assert d["report_sent"] is True
    assert d["evidence"]["vision"]["model"] == vision_agent.VISION_MODEL  # real output persisted
    print("   OK: Firestore doc is RESOLVED with every nested finding, real Vision output persisted")
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
