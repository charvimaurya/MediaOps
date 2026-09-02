"""One-shot deterministic fallback through the normal guarded pipeline.

    python3 fallback.py <incident_id>
"""

from __future__ import annotations

import sys
from typing import Callable

from control_plane import execute_incident
from incident_recorder import IncidentRecorder
from models import (
    Incident,
    IncidentStatus,
    RemediationAction,
    RemediationProposal,
    SafetyVerdict,
    VerificationResult,
    VerificationVerdict,
)
from safety_gate import ACTION_COMPATIBILITY, run_for_incident as run_safety_gate
from verify_recovery import run_for_incident as run_verification


FALLBACK_PRIORITY = {
    # Fixed enum-only order. Safety Gate remains authoritative for policy.
    "encoder_overload": (
        RemediationAction.RESTART_ENCODER,
        RemediationAction.REDUCE_PROFILE,
    ),
    "network_degradation": (RemediationAction.SWITCH_SOURCE,),
    "encoder_failure": (
        RemediationAction.RESTART_ENCODER,
        RemediationAction.SWITCH_SOURCE,
        RemediationAction.FAILOVER,
    ),
}


class FallbackStopped(RuntimeError):
    """Fallback cannot safely continue."""


def _alternate_action(incident: Incident) -> RemediationAction | None:
    if incident.evidence is None:
        return None
    compatible = ACTION_COMPATIBILITY.get(incident.evidence.fault_class, frozenset())
    tried = set(incident.actions_attempted)
    if incident.execution is not None:
        tried.add(incident.execution.action)
    for action in FALLBACK_PRIORITY.get(incident.evidence.fault_class.value, ()):
        if action in compatible and action not in tried:
            return action
    return None


def _mark_failed(recorder: IncidentRecorder, incident_id: str, reason: str) -> Incident:
    incident = recorder.load(incident_id)
    incident.status = IncidentStatus.AUTOMATION_FAILED
    incident.current_step = "fallback"
    incident.automation_failure_reason = reason
    incident.notes.append(f"automation failed: {reason}")
    recorder.save(incident)
    return incident


def run_fallback(
    incident_id: str,
    *,
    recorder: IncidentRecorder | None = None,
    gate_runner: Callable[[str], object] = run_safety_gate,
    control_runner: Callable[[str], object] = execute_incident,
    verify_runner: Callable[[str], VerificationResult] = run_verification,
) -> VerificationResult:
    """Try exactly one alternate action, with no gate or control bypass."""
    active_recorder = recorder or IncidentRecorder()
    try:
        incident = active_recorder.load(incident_id)
        if incident.verification is None:
            raise FallbackStopped("VerificationResult is missing")
        if incident.verification.verdict is not VerificationVerdict.RECOVERY_FAILED:
            raise FallbackStopped(
                f"fallback requires RECOVERY_FAILED, got {incident.verification.verdict.value}"
            )
        if incident.fallback_attempted:
            raise FallbackStopped("fallback was already attempted; retries are capped at one")

        alternate = _alternate_action(incident)
        if alternate is None:
            raise FallbackStopped("no untried compatible alternate action remains")

        if incident.execution is not None:
            incident.execution_history.append(incident.execution)
        incident.verification_history.append(incident.verification)
        confidence = min(
            incident.proposal.confidence if incident.proposal is not None else 0.0,
            incident.evidence.confidence if incident.evidence is not None else 0.0,
        )
        incident.proposal = RemediationProposal(
            incident_id=incident.incident_id,
            action=alternate,
            rationale=(
                "Deterministic one-shot fallback after independently verified "
                "recovery failure"
            ),
            confidence=confidence,
            model="deterministic-fallback",
        )
        incident.fallback_attempted = True
        incident.verification = None
        incident.safety_decision = None
        incident.idempotency_key = None
        incident.current_step = "fallback"
        incident.notes.append(f"selected one fallback candidate: {alternate.value}")
        active_recorder.save(incident)

        decision = gate_runner(incident_id)
        if decision.verdict is not SafetyVerdict.ALLOW:
            raise FallbackStopped(f"Safety Gate blocked fallback: {decision.block_reason}")

        execution = control_runner(incident_id)
        if not execution.success:
            raise FallbackStopped(f"fallback execution failed: {execution.detail}")

        verification = verify_runner(incident_id)
        if verification.verdict is not VerificationVerdict.RECOVERED:
            raise FallbackStopped(
                f"fallback did not recover: {verification.verdict.value}; "
                f"{'; '.join(verification.failed_checks)}"
            )
        return verification
    except FallbackStopped as exc:
        _mark_failed(active_recorder, incident_id, str(exc))
        raise
    except Exception as exc:
        reason = f"fallback error: {type(exc).__name__}: {exc}"
        try:
            _mark_failed(active_recorder, incident_id, reason)
        except Exception as persist_exc:
            raise FallbackStopped(
                f"{reason}; additionally could not persist failure: {persist_exc}"
            ) from exc
        raise FallbackStopped(reason) from exc


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 fallback.py <incident_id>")
    try:
        result = run_fallback(sys.argv[1])
    except (KeyError, FallbackStopped) as exc:
        sys.exit(f"AUTOMATION_FAILED: {exc}")
    print(result.model_dump_json(indent=2))
