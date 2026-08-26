"""
IncidentDetector: polls Prometheus on a fixed interval, applies
detector/rules.py's deterministic rules, and creates Incident objects
through on_incident() when a fault is confirmed. No AI, no remediation --
on_incident is the seam the Master Agent attaches to in a later phase.
"""

import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

from detector.models import Incident, IncidentType, IncidentStatus, Severity
from detector.prometheus import PrometheusClient
from detector import rules
from detector.metrics import INCIDENTS_TOTAL, OPEN_INCIDENTS

logger = logging.getLogger(__name__)

# Consecutive breaching samples required before an incident is created,
# per type.
CONFIRMATION_COUNTS = {
    IncidentType.ENCODER_FAILURE: 1,
    IncidentType.NETWORK_DEGRADATION: 3,
    IncidentType.ENCODER_OVERLOAD: 3,
}

SEVERITY_MAP = {
    IncidentType.ENCODER_FAILURE: Severity.CRITICAL,
    IncidentType.NETWORK_DEGRADATION: Severity.HIGH,
    IncidentType.ENCODER_OVERLOAD: Severity.HIGH,
}

HEALTHY_CLEAR_STREAK = 3


def format_reason(incident_type: IncidentType, telemetry: dict) -> str:
    """Plain-English reason including the values that triggered it."""
    if incident_type == IncidentType.ENCODER_FAILURE:
        return (
            f"encoder_status={telemetry.get('encoder_status')} "
            f"fps={telemetry.get('fps')} bitrate={telemetry.get('bitrate')}"
        )
    if incident_type == IncidentType.NETWORK_DEGRADATION:
        return (
            f"packet_loss={telemetry.get('packet_loss')} "
            f"network_latency={telemetry.get('network_latency')}"
        )
    if incident_type == IncidentType.ENCODER_OVERLOAD:
        return (
            f"cpu_usage={telemetry.get('cpu_usage')} "
            f"encoding_latency={telemetry.get('encoding_latency')} "
            f"fps={telemetry.get('fps')}"
        )
    return "unrecognised incident type"


def compact_telemetry(telemetry: dict) -> str:
    parts = []
    for key in ("cpu_usage", "fps", "encoding_latency", "packet_loss", "network_latency", "encoder_status"):
        if key in telemetry:
            parts.append(f"{key}={telemetry[key]}")
    return " ".join(parts)


class IncidentDetector:
    def __init__(
        self,
        prom_client: PrometheusClient,
        on_incident: Callable[[Incident], None],
        poll_interval: float = 0.5,
    ):
        self._prom_client = prom_client
        self._on_incident = on_incident
        self._poll_interval = poll_interval

        self._breach_counts: Dict[IncidentType, int] = {t: 0 for t in IncidentType}
        self._healthy_streak = 0
        self._open_incidents: Dict[IncidentType, Incident] = {}
        self._next_incident_number = 1

        self._stop_event = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------

    def start(self) -> threading.Thread:
        self._thread = threading.Thread(target=self._run_forever, daemon=True)
        self._thread.start()
        return self._thread

    def stop(self) -> None:
        self._stop_event.set()

    def force_clear(self) -> None:
        """Reset all detector state -- for resetting between demo runs."""
        self._breach_counts = {t: 0 for t in IncidentType}
        self._healthy_streak = 0
        self._open_incidents = {}
        for t in IncidentType:
            OPEN_INCIDENTS.labels(type=t.value).set(0)

    # ------------------------------------------------------------
    # loop
    # ------------------------------------------------------------

    def run_iteration(self) -> None:
        """One full iteration: fetch telemetry, evaluate, never raises.
        This is what the background loop calls each tick; tests call it
        directly for deterministic, non-threaded verification."""
        try:
            telemetry = self._prom_client.get_telemetry()
            self.evaluate(telemetry)
        except Exception:
            logger.exception("Incident detector iteration failed; continuing")

    def _run_forever(self) -> None:
        while not self._stop_event.is_set():
            self.run_iteration()
            self._stop_event.wait(self._poll_interval)

    # ------------------------------------------------------------
    # core logic
    # ------------------------------------------------------------

    def evaluate(self, telemetry: Dict[str, float]) -> None:
        incident_type = rules.evaluate(telemetry)

        if incident_type is None:
            for t in self._breach_counts:
                self._breach_counts[t] = 0

            if rules.is_healthy(telemetry):
                self._healthy_streak += 1
                if self._healthy_streak >= HEALTHY_CLEAR_STREAK:
                    self._mark_candidates_clear()
            else:
                self._healthy_streak = 0
            return

        self._healthy_streak = 0

        # Only the matched type's counter advances; a sample breaching a
        # different type resets everyone else's.
        for t in self._breach_counts:
            if t != incident_type:
                self._breach_counts[t] = 0
        self._breach_counts[incident_type] += 1

        existing = self._open_incidents.get(incident_type)
        if existing is not None:
            # Dedup: update the existing OPEN incident instead of
            # creating a second one for the same type.
            existing.telemetry_snapshot = telemetry
            existing.breach_count = self._breach_counts[incident_type]
            return

        required = CONFIRMATION_COUNTS[incident_type]
        if self._breach_counts[incident_type] < required:
            return

        incident = self._create_incident(incident_type, telemetry)
        self._open_incidents[incident_type] = incident
        self._breach_counts[incident_type] = 0

        INCIDENTS_TOTAL.labels(type=incident_type.value).inc()
        OPEN_INCIDENTS.labels(type=incident_type.value).set(1)

        self._on_incident(incident)

    def _mark_candidates_clear(self) -> None:
        for incident in self._open_incidents.values():
            if incident.status == IncidentStatus.OPEN and not incident.candidate_clear:
                incident.candidate_clear = True
                logger.info(
                    "CANDIDATE_CLEAR %s %s -- %d consecutive healthy samples, still OPEN",
                    incident.incident_id,
                    incident.type.value,
                    self._healthy_streak,
                )

    def _create_incident(self, incident_type: IncidentType, telemetry: dict) -> Incident:
        incident_id = f"INC-{self._next_incident_number:03d}"
        self._next_incident_number += 1

        return Incident(
            incident_id=incident_id,
            type=incident_type,
            severity=SEVERITY_MAP[incident_type],
            reason=format_reason(incident_type, telemetry),
            status=IncidentStatus.OPEN,
            created_at=datetime.now(timezone.utc),
            resolved_at=None,
            telemetry_snapshot=dict(telemetry),
            breach_count=CONFIRMATION_COUNTS[incident_type],
        )


# ============================================================
# ENTRY POINT -- run via `python3 -m detector.detector`, mirroring how
# simulator/pipeline.py is run via `python3 -m simulator.pipeline`.
# ============================================================

def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from prometheus_client import start_http_server

    # Wiring only (section J): the Master Agent and its dependencies are
    # built here and handed the existing on_incident seam. Nothing in
    # IncidentDetector/rules/prometheus above this point changes.
    from agent.master_agent import MasterAgent
    from agent.diagnosis import StubDiagnosisProvider
    from agent.knowledge import KnowledgeBase
    from agent.executor import RealActionExecutor
    from agent.verification import Verifier

    metrics_port = 8002
    start_http_server(metrics_port)
    logger.info("Detector metrics: http://localhost:%d/metrics", metrics_port)

    incidents: list = []

    prom_client = PrometheusClient()
    master_agent = MasterAgent(
        diagnosis_provider=StubDiagnosisProvider(),
        knowledge_base=KnowledgeBase(),
        action_executor=RealActionExecutor(),
        verifier=Verifier(prom_client),
    )

    def on_incident(incident: Incident) -> None:
        logger.info(
            "INCIDENT %s %s %s %s",
            incident.incident_id,
            incident.type.value,
            incident.severity.value,
            compact_telemetry(incident.telemetry_snapshot),
        )
        incidents.append(incident)

        def _run_agent() -> None:
            outcome = master_agent.handle_incident(incident)
            logger.info(
                "[MasterAgent] Incident %s finished: final_state=%s attempts=%d actions=%s rto=%s",
                outcome.incident_id, outcome.final_state.value, outcome.attempts,
                outcome.actions_tried, outcome.rto_seconds,
            )

        threading.Thread(target=_run_agent, daemon=True).start()

    detector = IncidentDetector(prom_client, on_incident)
    detector.start()

    logger.info("Incident detector started. Press CTRL+C to stop.")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        logger.info("Stopping incident detector...")
        detector.stop()


if __name__ == "__main__":
    main()
