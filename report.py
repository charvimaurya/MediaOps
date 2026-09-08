"""Send one authority-free Slack summary for a completed incident.

    python3 report.py <incident_id>
"""

from __future__ import annotations

import json
import os
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from typing import Callable

from google.cloud import firestore
import certifi

from incident_recorder import IncidentRecorder
from models import Incident
from observability import log_event


class ReportError(RuntimeError):
    pass


def build_message(incident: Incident) -> str:
    """Create a factual summary without making or authorising any decision."""
    if (
        incident.verification is None
        and incident.automation_failure_reason is None
        and incident.terminal_reason is None
    ):
        raise ReportError("incident has no final verification or automation outcome")
    fault = (
        incident.evidence.fault_class.value
        if incident.evidence is not None
        else incident.anomaly.fault_class.value
    )
    diagnosis = incident.evidence.summary if incident.evidence is not None else incident.anomaly.reason
    actions = [action.value for action in incident.actions_attempted]
    if not actions and incident.execution is not None:
        actions = [incident.execution.action.value]
    if incident.automation_failure_reason:
        verdict = f"AUTOMATION_FAILED — {incident.automation_failure_reason}"
    elif incident.terminal_reason:
        verdict = f"{incident.status.value} — {incident.terminal_reason}"
    else:
        verdict = incident.verification.verdict.value
    return (
        f"MediaOps incident {incident.incident_id}\n"
        f"Fault: {fault}\n"
        f"Diagnosis: {diagnosis}\n"
        f"Actions attempted: {', '.join(actions) if actions else 'none'}\n"
        f"Final verdict: {verdict}"
    )


def _claim_once(recorder: IncidentRecorder, incident_id: str) -> Incident:
    ref = recorder._col.document(incident_id)
    transaction = recorder._db.transaction()

    @firestore.transactional
    def claim(txn):
        snapshot = ref.get(transaction=txn)
        if not snapshot.exists:
            raise KeyError(f"no incident {incident_id} in Firestore")
        incident = Incident.model_validate(snapshot.to_dict())
        if incident.report_sent:
            raise ReportError("report was already sent")
        if incident.report_claimed:
            raise ReportError("report delivery is already claimed")
        incident.report_claimed = True
        incident.report_error = None
        incident.updated_at = datetime.now(timezone.utc)
        txn.set(ref, incident.model_dump(mode="json"))
        return incident

    return claim(transaction)


def _send_slack(webhook_url: str, message: str) -> None:
    request = urllib.request.Request(
        webhook_url,
        data=json.dumps({"text": message}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    ca_bundle = os.environ.get("SLACK_CA_BUNDLE", certifi.where())
    tls_context = ssl.create_default_context(cafile=ca_bundle)
    with urllib.request.urlopen(
        request, timeout=10, context=tls_context
    ) as response:
        if not 200 <= response.status < 300:
            raise ReportError(f"Slack returned HTTP {response.status}")


def run_report(
    incident_id: str,
    *,
    recorder: IncidentRecorder | None = None,
    sender: Callable[[str, str], None] = _send_slack,
    webhook_url: str | None = None,
) -> dict[str, str | bool]:
    active_recorder = recorder or IncidentRecorder()
    log_event("report", incident_id, "report", "started")
    url = webhook_url if webhook_url is not None else os.environ.get("SLACK_WEBHOOK_URL")
    if not url:
        raise ReportError("SLACK_WEBHOOK_URL is missing")
    incident = _claim_once(active_recorder, incident_id)
    try:
        message = build_message(incident)
        sender(url, message)
        incident = active_recorder.load(incident_id)
        incident.report_claimed = False
        incident.report_sent = True
        incident.report_error = None
        incident.notes.append("final incident report sent to Slack")
        active_recorder.save(incident)
        log_event("report", incident_id, "report", "sent", detail="Slack report sent")
        return {"incident_id": incident_id, "sent": True, "message": message}
    except Exception as exc:
        try:
            incident = active_recorder.load(incident_id)
            incident.report_claimed = False
            incident.report_error = f"{type(exc).__name__}: {exc}"
            active_recorder.save(incident)
        except Exception as persist_exc:
            raise ReportError(
                f"report failed: {exc}; could not persist failure: {persist_exc}"
            ) from exc
        log_event("report", incident_id, "report", "failed",
                  detail=f"{type(exc).__name__}: {exc}")
        raise ReportError(f"report failed: {type(exc).__name__}: {exc}") from exc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 report.py <incident_id>")
    try:
        result = run_report(sys.argv[1])
    except (KeyError, ReportError) as exc:
        sys.exit(f"REPORT_NOT_SENT: {exc}")
    print(json.dumps(result, indent=2))
