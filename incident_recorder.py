"""
The Incident Recorder -- turns an AnomalyEvent into a durable Firestore Incident.

Rules (from CLAUDE.md -- "Firestore is authoritative state"):

- One active incident per problem. If a non-terminal incident already exists for
  the same fault, ATTACH the new event to it and return that id. Never duplicate.
- The Firestore write must be confirmed before `record()` returns. A write
  failure raises -- it never looks like success, and nothing downstream runs.

For now this component only creates/attaches and returns the id. No other
component is called.

Prereqs to run for real:
    pip install google-cloud-firestore
    gcloud auth application-default login
    # project defaults to broadcast-ops-copilot; override with GCP_PROJECT_ID
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

from google.cloud import firestore
from google.cloud.firestore_v1 import FieldFilter

from models import (
    TERMINAL_STATUSES,
    AnomalyEvent,
    FaultClass,
    Incident,
    IncidentStatus,
)
from observability import record_event

logger = logging.getLogger("incident_recorder")

# --------------------------------------------------------------------------- #
# Config -- inline os.environ, matching infra_health_monitor.py's style
# --------------------------------------------------------------------------- #

# Best-effort .env load so a local `.env` "just works"; harmless if absent.
try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "broadcast-ops-copilot")
FIRESTORE_DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "(default)")
INCIDENTS_COLLECTION = os.environ.get("FIRESTORE_COLLECTION", "incidents")

# The 6 non-terminal statuses -- an incident in any of these is still "active".
ACTIVE_STATUSES = [s.value for s in IncidentStatus if s not in TERMINAL_STATUSES]


# --------------------------------------------------------------------------- #
# The recorder
# --------------------------------------------------------------------------- #

class IncidentRecorder:
    """Records AnomalyEvents as durable Firestore Incident documents."""

    def __init__(
        self,
        client: Optional["firestore.Client"] = None,
        collection_name: str = INCIDENTS_COLLECTION,
    ) -> None:
        # `client` is injectable so tests can pass a stub (e.g. one whose write
        # raises, to prove the fail-safe path). Real use goes to Firestore.
        self._db = client or firestore.Client(
            project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE_ID
        )
        self._col = self._db.collection(collection_name)

    # -- public entry point --------------------------------------------------- #

    def record(self, event: AnomalyEvent) -> str:
        """
        Create a new Incident for `event`, or attach `event` to the existing
        active incident for the same fault. Returns the incident_id.

        Raises (does NOT return an id) if the Firestore write cannot be
        confirmed.
        """
        existing = self._find_active(event.fault_class)
        if existing is not None:
            self._attach(existing, event)
            logger.info(
                "attached anomaly %s to active incident %s",
                event.event_id, existing.incident_id,
            )
            return existing.incident_id

        incident = Incident(anomaly=event, current_step="record")
        record_event(incident, "infra_health_monitor", "detect", "incident_recorded",
                     status=IncidentStatus.DETECTED, detail=event.reason)
        self._create(incident)
        logger.info("created incident %s for anomaly %s", incident.incident_id, event.event_id)
        return incident.incident_id

    # -- load / save (the shared Firestore-incidents gateway) ------------- #

    def load(self, incident_id: str) -> Incident:
        """Read one incident back from Firestore. Raises KeyError if missing."""
        snap = self._col.document(incident_id).get()
        if not snap.exists:
            raise KeyError(f"no incident {incident_id} in Firestore")
        return Incident.model_validate(snap.to_dict())

    def save(self, incident: Incident) -> None:
        """
        Overwrite the whole incident document, then read it back and confirm the
        new status landed. Used by the Orchestrator after every transition --
        the read-back is the "state is durable before we continue" guarantee.
        """
        incident.updated_at = datetime.now(timezone.utc)
        ref = self._col.document(incident.incident_id)
        ref.set(incident.model_dump(mode="json"))  # sync; raises on failure

        snap = ref.get()
        if not snap.exists or (snap.to_dict() or {}).get("status") != incident.status.value:
            raise RuntimeError(
                f"Firestore save for incident {incident.incident_id} was not confirmed"
            )

    # -- dedup lookup ------------------------------------------------------- #

    def _find_active(self, fault_class: FaultClass) -> Optional[Incident]:
        """
        Newest non-terminal incident whose fault class matches, or None.

        Single-field `in` filter on status -> uses the automatic index, no
        composite index needed. Fault-class match and recency are done in
        Python (the collection is tiny; single stream).
        """
        query = self._col.where(filter=FieldFilter("status", "in", ACTIVE_STATUSES))
        candidates: list[Incident] = []
        for snap in query.stream():
            data = snap.to_dict()
            if data is None:
                continue
            if data.get("anomaly", {}).get("fault_class") != fault_class.value:
                continue
            try:
                candidates.append(Incident.model_validate(data))
            except Exception:
                logger.exception("skipping unparseable incident doc %s", snap.id)

        if not candidates:
            return None
        return max(candidates, key=lambda inc: inc.created_at)

    # -- writes (each confirmed before returning) ------------------------- #

    def _create(self, incident: Incident) -> None:
        ref = self._col.document(incident.incident_id)
        ref.set(incident.model_dump(mode="json"))  # sync; raises on failure

        snap = ref.get()  # read-back: the write must be visible
        if not snap.exists:
            raise RuntimeError(
                f"Firestore write for incident {incident.incident_id} was not confirmed"
            )

    def _attach(self, incident: Incident, event: AnomalyEvent) -> None:
        incident.repeated_anomalies.append(event)
        incident.updated_at = datetime.now(timezone.utc)
        incident.notes.append(
            f"re-observed anomaly {event.event_id} at {event.detected_at.isoformat()} "
            f"(breach_count={event.breach_count})"
        )

        ref = self._col.document(incident.incident_id)
        ref.update({  # sync; raises if the doc is gone or on any failure
            "repeated_anomalies": [a.model_dump(mode="json") for a in incident.repeated_anomalies],
            "updated_at": incident.updated_at.isoformat(),
            "notes": incident.notes,
        })

        snap = ref.get()  # read-back: confirm the append landed
        stored = (snap.to_dict() or {}).get("repeated_anomalies", [])
        if len(stored) != len(incident.repeated_anomalies):
            raise RuntimeError(
                f"Firestore update for incident {incident.incident_id} was not confirmed"
            )


# --------------------------------------------------------------------------- #
# Convenience + manual sanity run
# --------------------------------------------------------------------------- #

def record_incident(event: AnomalyEvent) -> str:
    """Module-level shortcut using a default recorder."""
    return IncidentRecorder().record(event)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from models import Severity

    sample = AnomalyEvent(
        fault_class=FaultClass.UNKNOWN,
        severity=Severity.HIGH,
        reason="media_pipeline_health=0 sustained 15s (6 consecutive polls, window 15s)",
        health_value=0,
        telemetry_snapshot={"media_fps": 18.0, "media_cpu_usage_percent": 97.0},
        breach_count=6,
    )
    recorder = IncidentRecorder()
    incident_id = recorder.record(sample)
    print(f"\nincident_id = {incident_id}\n")

    stored = recorder._col.document(incident_id).get().to_dict()
    print(Incident.model_validate(stored).model_dump_json(indent=2))
