"""Read-only incident lifecycle viewer.

Usage: python3 trace.py <incident_id>
"""

from __future__ import annotations

import sys
from datetime import datetime

from incident_recorder import IncidentRecorder
from models import Incident, LifecycleEvent


def _time(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds")


def _inferred_events(incident: Incident) -> list[LifecycleEvent]:
    """Provide useful chronology for records created before lifecycle_events."""
    events = [LifecycleEvent(timestamp=incident.anomaly.detected_at, component="detector",
                             step="detect", outcome="detected", status="DETECTED",
                             detail=incident.anomaly.reason)]
    artifacts = (
        (incident.vision, "observed_at", "vision", "diagnose", "completed"),
        (incident.infra, "observed_at", "infra", "diagnose", "completed"),
        (incident.evidence, "aggregated_at", "aggregator", "aggregate", "completed"),
        (incident.proposal, "proposed_at", "remediation", "propose", "completed"),
        (incident.safety_decision, "evaluated_at", "safety_gate", "gate", "completed"),
    )
    for artifact, attr, component, step, outcome in artifacts:
        if artifact is not None:
            events.append(LifecycleEvent(timestamp=getattr(artifact, attr), component=component,
                                         step=step, outcome=outcome))
    for result in [*incident.execution_history, *([incident.execution] if incident.execution else [])]:
        events.append(LifecycleEvent(timestamp=result.executed_at, component="control_plane",
                                     step="execute", outcome="succeeded" if result.success else "failed",
                                     action=result.action, detail=result.detail))
    for result in [*incident.verification_history, *([incident.verification] if incident.verification else [])]:
        events.append(LifecycleEvent(timestamp=result.verified_at, component="verify", step="verify",
                                     outcome=result.verdict.value.lower(), action=result.action,
                                     detail="; ".join(result.failed_checks)))
    if incident.closed_at:
        events.append(LifecycleEvent(timestamp=incident.closed_at, component="orchestrator",
                                     step=incident.current_step, outcome=incident.status.value.lower(),
                                     status=incident.status, detail=incident.terminal_reason or ""))
    return events


def render_trace(incident: Incident) -> str:
    lines = [f"Incident {incident.incident_id}", "", "TIMELINE"]
    events = incident.lifecycle_events or _inferred_events(incident)
    for event in sorted(events, key=lambda item: item.timestamp):
        status = f" status={event.status.value}" if event.status else ""
        action = f" action={event.action.value}" if event.action else ""
        detail = f" — {event.detail}" if event.detail else ""
        lines.append(f"{_time(event.timestamp)}  {event.component:<16} {event.step:<18} {event.outcome}{status}{action}{detail}")

    lines.extend(["", "DIAGNOSIS"])
    if incident.vision:
        lines.append(f"Vision: {incident.vision.symptom.value} confidence={incident.vision.confidence:.3f} — {incident.vision.description}")
    else:
        lines.append("Vision: missing")
    if incident.infra:
        lines.append(f"Infra: {incident.infra.fault_class.value} target={incident.infra.affected_component} confidence={incident.infra.confidence:.3f} — {incident.infra.description}")
    else:
        lines.append("Infra: missing")
    lines.append(f"Agreement: {incident.evidence.agreement if incident.evidence else 'not aggregated'}")

    lines.extend(["", "RETRIEVED PRECEDENT"])
    if incident.precedent:
        for match in incident.precedent:
            lines.append(f"- {match.kb_id}: {match.action_taken.value}, outcome={match.outcome}, similarity={match.similarity:.4f}")
    else:
        lines.append("None")

    lines.extend(["", "PROPOSAL"])
    proposals = [*incident.proposal_history, *([incident.proposal] if incident.proposal else [])]
    if proposals:
        for proposal in proposals:
            lines.append(f"- {_time(proposal.proposed_at)} {proposal.action.value} confidence={proposal.confidence:.3f} — {proposal.rationale}")
    else:
        lines.append("Missing")

    lines.extend(["", "SAFETY GATE"])
    decisions = [*incident.safety_decision_history,
                 *([incident.safety_decision] if incident.safety_decision else [])]
    if decisions:
        for number, decision in enumerate(decisions, 1):
            lines.append(f"Decision {number}: {_time(decision.evaluated_at)} action={decision.action.value}")
            for check in decision.checks:
                lines.append(f"  - {check.name}: {'PASS' if check.passed else 'FAIL'} — {check.detail}")
            lines.append(f"  Verdict: {decision.verdict.value}" +
                         (f" — {decision.block_reason}" if decision.block_reason else ""))
    else:
        lines.append("No decision")

    lines.extend(["", "EXECUTION"])
    executions = [*incident.execution_history, *([incident.execution] if incident.execution else [])]
    if executions:
        for result in executions:
            lines.append(f"- {_time(result.executed_at)} {result.action.value}: success={result.success}, key={result.idempotency_key} — {result.detail}")
    else:
        lines.append("None")

    lines.extend(["", "VERIFICATION"])
    verifications = [*incident.verification_history, *([incident.verification] if incident.verification else [])]
    if verifications:
        for result in verifications:
            lines.append(f"- {_time(result.verified_at)} {result.verdict.value}: telemetry={result.telemetry_ok}, video={result.video_ok}, window={result.stable_window_seconds:.1f}s")
            for index, sample in enumerate(result.samples, 1):
                rendered = ", ".join(f"{key}={value}" for key, value in sample.items())
                lines.append(f"    sample {index}: {rendered}")
            if result.failed_checks:
                lines.append(f"    reasons: {'; '.join(result.failed_checks)}")
    else:
        lines.append("None")

    lines.extend(["", "FALLBACK / OUTPUTS",
                  f"Fallback attempted: {incident.fallback_attempted}",
                  f"Report: {'sent' if incident.report_sent else 'not sent'}" + (f" — {incident.report_error}" if incident.report_error else ""),
                  f"KB writeback: {incident.kb_writeback_id or 'none'}" + (f" — {incident.kb_writeback_error}" if incident.kb_writeback_error else ""),
                  "", "FINAL OUTCOME",
                  f"Status: {incident.status.value}",
                  f"Stopped at: {incident.terminal_step or 'none'}",
                  f"Reason: {incident.terminal_reason or incident.automation_failure_reason or 'none'}"])
    return "\n".join(lines)


def trace_incident(incident_id: str, *, recorder: IncidentRecorder | None = None) -> str:
    return render_trace((recorder or IncidentRecorder()).load(incident_id))


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 trace.py <incident_id>")
    try:
        print(trace_incident(sys.argv[1]))
    except KeyError as exc:
        sys.exit(f"TRACE_NOT_FOUND: {exc}")
