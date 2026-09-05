"""Production-style web console over the existing MediaOps workflow.

The browser never receives Firestore credentials and has no operational
authority. This server reads Firestore/Prometheus, proxies four predefined
simulator controls, and starts the existing Detector -> Orchestrator workflow.

Run with: uvicorn web.ops_console:app --port 8081
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

from detector import Detector, PROMETHEUS_URL, query_health, query_raw_snapshot
from incident_recorder import IncidentRecorder
from models import Incident, IncidentStatus, TERMINAL_STATUSES, VerificationVerdict
from observability import log_event
from orchestrator import Orchestrator
from pdf_report import PDFReportError, build_incident_pdf


logger = logging.getLogger("ops_console")
SIMULATOR_URL = os.environ.get("SIMULATOR_CONTROL_URL", "http://localhost:8001").rstrip("/")
SIMULATOR_TIMEOUT_SECONDS = float(os.environ.get("SIMULATOR_CONTROL_TIMEOUT_SECONDS", "10"))
CONSOLE_DETECTION_TIMEOUT_SECONDS = float(os.environ.get("CONSOLE_DETECTION_TIMEOUT_SECONDS", "90"))
GRAFANA_EMBED_URL = os.environ.get(
    "GRAFANA_EMBED_URL",
    "http://localhost:3000/d/mediaops/mediaops?orgId=1&refresh=5s&theme=dark&kiosk",
)
PAGE = Path(__file__).parent / "static" / "index.html"
EVIDENCE_VIDEO = Path(__file__).parents[1] / "simulator" / "output" / "messy_video.mov"

FAULT_ENDPOINTS = {
    "encoder-overload": "/failure/encoder-overload",
    "encoder-failure": "/failure/encoder-crash",
    "network-degradation": "/failure/network-degradation",
    "reset": "/recovery/reset",
}

app = FastAPI(title="MediaOps CoPilot Console")
_workflow_lock = threading.Lock()
_workflow_active = False
_active_incident_id: str | None = None


def get_recorder() -> IncidentRecorder:
    return IncidentRecorder()


def simulator_request(path: str, *, method: str = "GET") -> dict:
    request = urllib.request.Request(f"{SIMULATOR_URL}{path}", method=method)
    try:
        with urllib.request.urlopen(request, timeout=SIMULATOR_TIMEOUT_SECONDS) as response:
            return json.loads(response.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=502, detail=f"simulator unavailable: {exc}") from exc


def all_incidents(recorder: IncidentRecorder) -> list[Incident]:
    """Return every valid Firestore incident, newest first."""
    incidents: list[Incident] = []
    for snapshot in recorder._col.stream():
        try:
            incidents.append(Incident.model_validate(snapshot.to_dict()))
        except Exception:
            logger.warning("ignoring malformed Firestore incident %s", getattr(snapshot, "id", "?"))
    return sorted(incidents, key=lambda item: item.updated_at, reverse=True)


def latest_incident(recorder: IncidentRecorder) -> Incident | None:
    incidents = all_incidents(recorder)
    return incidents[0] if incidents else None


def incident_mttr_seconds(incident: Incident) -> float | None:
    """Return detection-to-verified-recovery duration for one incident."""
    if (
        incident.verification is None
        or incident.verification.verdict is not VerificationVerdict.RECOVERED
    ):
        return None
    detected_at = incident.anomaly.detected_at or incident.created_at
    return round(
        max(0.0, (incident.verification.verified_at - detected_at).total_seconds()),
        1,
    )


def incident_summary(incident: Incident) -> dict:
    fault = incident.evidence.fault_class if incident.evidence else incident.anomaly.fault_class
    return {
        "incident_id": incident.incident_id,
        "fault_class": fault.value,
        "status": incident.status.value,
        "created_at": incident.created_at.isoformat(),
        "updated_at": incident.updated_at.isoformat(),
        "closed_at": incident.closed_at.isoformat() if incident.closed_at else None,
        "current_step": incident.current_step,
        "mttr_seconds": incident_mttr_seconds(incident),
    }


def incident_stats(incidents: list[Incident]) -> dict:
    terminal = [item for item in incidents if item.status in TERMINAL_STATUSES]
    auto_resolved = [
        item
        for item in terminal
        if item.status is IncidentStatus.CLOSED and incident_mttr_seconds(item) is not None
    ]
    durations = [duration for item in auto_resolved if (duration := incident_mttr_seconds(item)) is not None]
    return {
        "total_incidents": len(incidents),
        "terminal_incidents": len(terminal),
        "auto_resolved_percent": round(100 * len(auto_resolved) / len(terminal), 1) if terminal else 0.0,
        "average_mttr_seconds": round(sum(durations) / len(durations), 1) if durations else None,
    }


def prometheus_metrics() -> dict:
    health = query_health(PROMETHEUS_URL)
    raw = query_raw_snapshot(PROMETHEUS_URL)
    if health is None:
        raise HTTPException(status_code=503, detail="Prometheus health metric unavailable")
    return {
        "health": health,
        "fps": raw.get("media_fps"),
        "cpu_percent": raw.get("media_cpu_usage_percent"),
        "packet_loss_percent": raw.get("media_packet_loss_percent"),
        "encoder_status": raw.get("media_encoder_status"),
        "status": "HEALTHY" if health == 1 else "UNHEALTHY",
        "observed_at": datetime.now(timezone.utc).isoformat(),
    }


def _detect_and_orchestrate(workflow_claimed: bool = False) -> None:
    """Run the proven workflow components after a console fault injection."""
    global _workflow_active, _active_incident_id
    if not workflow_claimed:
        with _workflow_lock:
            if _workflow_active:
                logger.info("console workflow already monitoring an injected fault")
                return
            _workflow_active = True
    try:
        recorder = IncidentRecorder()
        incident_id: str | None = None

        def record(event) -> None:
            nonlocal incident_id
            global _active_incident_id
            incident_id = recorder.record(event)
            with _workflow_lock:
                _active_incident_id = incident_id
            log_event("ops_console", incident_id, "trigger_workflow", "detected")

        detector = Detector(on_anomaly=record)
        deadline = time.monotonic() + CONSOLE_DETECTION_TIMEOUT_SECONDS
        while incident_id is None and time.monotonic() < deadline:
            detector.poll_once()
            if incident_id is None:
                time.sleep(detector._poll_interval)
        if incident_id is None:
            logger.error("console-triggered workflow timed out waiting for unhealthy metrics")
            return
        Orchestrator(recorder=recorder).run(incident_id)
    except Exception:
        logger.exception("console-triggered workflow failed")
    finally:
        with _workflow_lock:
            _workflow_active = False
            _active_incident_id = None


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    return FileResponse(PAGE)


@app.get("/api/health")
def console_health() -> dict:
    simulator = simulator_request("/health")
    health = query_health(PROMETHEUS_URL)
    return {
        "status": "monitoring" if health is not None else "degraded",
        "simulator": simulator,
        "prometheus": "connected" if health is not None else "unavailable",
        "workflow_active": _workflow_active,
        "active_incident_id": _active_incident_id,
    }


@app.get("/api/simulator")
def simulator_state() -> dict:
    return simulator_request("/state")


@app.get("/api/metrics")
def read_metrics() -> dict:
    return prometheus_metrics()


@app.get("/api/config")
def read_public_config() -> dict:
    return {"grafana_embed_url": GRAFANA_EMBED_URL}


@app.post("/api/fault/{fault}")
def inject_fault(fault: str, background_tasks: BackgroundTasks = None) -> dict:
    global _workflow_active, _active_incident_id
    endpoint = FAULT_ENDPOINTS.get(fault)
    if endpoint is None:
        raise HTTPException(status_code=404, detail="unknown predefined fault")
    starts_workflow = fault != "reset" and background_tasks is not None
    if starts_workflow:
        # Reserve the single workflow slot before touching the simulator. This
        # closes the race where a second fault could be injected while the
        # first incident was still running, then receive no detector worker.
        with _workflow_lock:
            if _workflow_active:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        "An incident is already running. Return to the current "
                        "incident and wait for it to finish."
                    ),
                )
            _workflow_active = True
            _active_incident_id = None
    try:
        result = simulator_request(endpoint, method="POST")
    except Exception:
        if starts_workflow:
            with _workflow_lock:
                _workflow_active = False
                _active_incident_id = None
        raise
    if starts_workflow:
        background_tasks.add_task(_detect_and_orchestrate, True)
    if background_tasks is None:
        return result
    return {**result, "workflow_started": fault != "reset"}


@app.get("/api/incidents")
def read_incidents(recorder: IncidentRecorder = Depends(get_recorder)) -> dict:
    incidents = all_incidents(recorder)
    return {
        "incidents": [incident_summary(item) for item in incidents],
        "stats": incident_stats(incidents),
    }


@app.get("/api/incidents/latest")
def read_latest(recorder: IncidentRecorder = Depends(get_recorder)) -> dict:
    incident = latest_incident(recorder)
    if incident is None:
        return {"incident": None}
    payload = incident.model_dump(mode="json")
    payload["mttr_seconds"] = incident_mttr_seconds(incident)
    return {"incident": payload}


@app.get("/api/incidents/{incident_id}")
def read_incident(incident_id: str, recorder: IncidentRecorder = Depends(get_recorder)) -> dict:
    try:
        incident = recorder.load(incident_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = incident.model_dump(mode="json")
    payload["mttr_seconds"] = incident_mttr_seconds(incident)
    return {"incident": payload}


@app.get("/api/incidents/{incident_id}/report.pdf", include_in_schema=False)
def download_incident_report(
    incident_id: str, recorder: IncidentRecorder = Depends(get_recorder)
) -> Response:
    """Generate a PDF only from authoritative, verified Firestore state."""
    try:
        incident = recorder.load(incident_id)
        content = build_incident_pdf(incident)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PDFReportError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": (
                f'attachment; filename="mediaops-incident-{incident.incident_id}.pdf"'
            )
        },
    )


@app.get("/api/video/evidence.mp4", include_in_schema=False)
def evidence_video() -> FileResponse:
    if not EVIDENCE_VIDEO.exists():
        raise HTTPException(status_code=404, detail="evidence video unavailable")
    return FileResponse(EVIDENCE_VIDEO, media_type="video/mp4")
