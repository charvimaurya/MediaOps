"""Thin web console over the simulator and Firestore incident state.

Run with:
    uvicorn web.ops_console:app --port 8081
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from incident_recorder import IncidentRecorder
from models import Incident


SIMULATOR_URL = os.environ.get("SIMULATOR_CONTROL_URL", "http://localhost:8001").rstrip("/")
SIMULATOR_TIMEOUT_SECONDS = float(os.environ.get("SIMULATOR_CONTROL_TIMEOUT_SECONDS", "10"))
PAGE = Path(__file__).parent / "static" / "index.html"

FAULT_ENDPOINTS = {
    "encoder-overload": "/failure/encoder-overload",
    "encoder-failure": "/failure/encoder-crash",
    "network-degradation": "/failure/network-degradation",
    "reset": "/recovery/reset",
}

app = FastAPI(title="MediaOps CoPilot Console")


def get_recorder() -> IncidentRecorder:
    return IncidentRecorder()


def simulator_request(path: str, *, method: str = "GET") -> dict:
    request = urllib.request.Request(f"{SIMULATOR_URL}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=SIMULATOR_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"simulator unavailable: {exc}") from exc


def latest_incident(recorder: IncidentRecorder) -> Incident | None:
    """Newest updated valid incident; malformed test documents are ignored."""
    incidents: list[Incident] = []
    for snapshot in recorder._col.stream():
        try:
            incidents.append(Incident.model_validate(snapshot.to_dict()))
        except Exception:
            continue
    return max(incidents, key=lambda item: item.updated_at) if incidents else None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PAGE)


@app.get("/api/simulator")
def simulator_state() -> dict:
    return simulator_request("/state")


@app.post("/api/fault/{fault}")
def inject_fault(fault: str) -> dict:
    endpoint = FAULT_ENDPOINTS.get(fault)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="unknown predefined fault")
    return simulator_request(endpoint, method="POST")


@app.get("/api/incidents/latest")
def read_latest(recorder: IncidentRecorder = Depends(get_recorder)) -> dict:
    incident = latest_incident(recorder)
    if incident is None:
        return {"incident": None}
    return {"incident": incident.model_dump(mode="json")}


@app.get("/api/incidents/{incident_id}")
def read_incident(
    incident_id: str, recorder: IncidentRecorder = Depends(get_recorder)
) -> dict:
    try:
        incident = recorder.load(incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"incident": incident.model_dump(mode="json")}
