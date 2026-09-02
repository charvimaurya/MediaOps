"""Write one verified-success precedent to the existing Knowledge Base.

    python3 kb_writeback.py <incident_id>
"""

from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from typing import Callable

from google.cloud import firestore

import knowledge_base
from incident_recorder import IncidentRecorder
from models import Incident, VerificationVerdict


class KBWritebackError(RuntimeError):
    pass


def build_precedent(incident: Incident) -> dict:
    verification = incident.verification
    if verification is None:
        raise KBWritebackError("VerificationResult is missing")
    if (
        verification.verdict is not VerificationVerdict.RECOVERED
        or not verification.recovered
        or not verification.telemetry_ok
        or not verification.video_ok
    ):
        raise KBWritebackError("incident was not positively verified RECOVERED in both domains")
    if incident.evidence is None or not incident.evidence.validation_passed:
        raise KBWritebackError("validated incident evidence is missing")

    executions = [*incident.execution_history]
    if incident.execution is not None:
        executions.append(incident.execution)
    matching = [
        result for result in executions
        if result.success and result.action is verification.action
    ]
    if not matching:
        raise KBWritebackError("no successful execution matches the verified action")

    target = incident.evidence.infra.affected_component
    summary = (
        f"{incident.evidence.summary} Verified RECOVERED after "
        f"{verification.action.value} on {target}; telemetry stayed healthy for "
        f"{verification.stable_window_seconds:.1f}s and video was normal."
    )
    kb_id = f"incident-{incident.incident_id}"
    return {
        "kb_id": kb_id,
        "source_incident_id": incident.incident_id,
        "fault_class": incident.evidence.fault_class.value,
        "action_taken": verification.action.value,
        "target": target,
        "outcome": "resolved",
        "summary": summary,
        "evidence_summary": incident.evidence.summary,
        "verification_summary": {
            "verdict": verification.verdict.value,
            "telemetry_ok": verification.telemetry_ok,
            "video_ok": verification.video_ok,
            "stable_window_seconds": verification.stable_window_seconds,
        },
        "verified_at": verification.verified_at.isoformat(),
        "written_at": datetime.now(timezone.utc).isoformat(),
    }


def _write_once(recorder: IncidentRecorder, record: dict) -> tuple[dict, bool]:
    ref = recorder._db.collection(knowledge_base.KB_COLLECTION).document(record["kb_id"])
    transaction = recorder._db.transaction()

    @firestore.transactional
    def write(txn):
        snapshot = ref.get(transaction=txn)
        if snapshot.exists:
            existing = snapshot.to_dict() or {}
            if existing.get("source_incident_id") != record["source_incident_id"]:
                raise KBWritebackError("KB id collision")
            return existing, False
        txn.set(ref, record)
        return record, True

    return write(transaction)


def run_writeback(
    incident_id: str,
    *,
    recorder: IncidentRecorder | None = None,
    embedder: Callable[..., list[float]] = knowledge_base._embed,
    writer: Callable[[IncidentRecorder, dict], tuple[dict, bool]] = _write_once,
) -> dict:
    active_recorder = recorder or IncidentRecorder()
    try:
        incident = active_recorder.load(incident_id)
        base = build_precedent(incident)
        vector = list(embedder(base["summary"], task_type="RETRIEVAL_DOCUMENT"))
        if not vector or any(not math.isfinite(float(value)) for value in vector):
            raise KBWritebackError("embedding is empty or contains a non-finite value")
        record = {
            **base,
            "embedding": [float(value) for value in vector],
            "embedding_model": knowledge_base.EMBED_MODEL,
            "embedding_task_type": "RETRIEVAL_DOCUMENT",
        }
        stored, created = writer(active_recorder, record)
        incident = active_recorder.load(incident_id)
        incident.kb_writeback_id = base["kb_id"]
        incident.kb_writeback_error = None
        incident.notes.append(
            f"KB precedent {'created' if created else 'already existed'}: {base['kb_id']}"
        )
        active_recorder.save(incident)
        return {"created": created, "precedent": stored}
    except Exception as exc:
        try:
            incident = active_recorder.load(incident_id)
            incident.kb_writeback_error = f"{type(exc).__name__}: {exc}"
            active_recorder.save(incident)
        except Exception:
            pass
        if isinstance(exc, (KeyError, KBWritebackError)):
            raise
        raise KBWritebackError(f"writeback failed: {type(exc).__name__}: {exc}") from exc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 kb_writeback.py <incident_id>")
    try:
        result = run_writeback(sys.argv[1])
    except (KeyError, KBWritebackError) as exc:
        sys.exit(f"KB_NOT_WRITTEN: {exc}")
    printable = {
        "created": result["created"],
        "kb_id": result["precedent"]["kb_id"],
        "fault_class": result["precedent"]["fault_class"],
        "action_taken": result["precedent"]["action_taken"],
        "outcome": result["precedent"]["outcome"],
        "embedding_dimensions": len(result["precedent"]["embedding"]),
    }
    print(json.dumps(printable, indent=2))
