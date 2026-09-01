"""Deterministic, fail-closed authorization gate for remediation proposals.

This module contains no AI integration.  It reads only validated incident state,
runs explicit policy checks, and grants authority only when every check passes.

    python3 safety_gate.py <incident_id>
"""

from __future__ import annotations

import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import IntEnum
from typing import Callable

from incident_recorder import IncidentRecorder
from models import (
    FaultClass,
    Incident,
    IncidentEvidence,
    IncidentStatus,
    RemediationAction,
    RemediationProposal,
    SafetyCheck,
    SafetyDecision,
    SafetyVerdict,
)


class BlastRadius(IntEnum):
    COMPONENT = 1
    PROFILE = 2
    STREAM = 3


ACTION_COMPATIBILITY = {
    FaultClass.ENCODER_OVERLOAD: frozenset({
        RemediationAction.RESTART_ENCODER,
        RemediationAction.REDUCE_PROFILE,
    }),
    FaultClass.NETWORK_DEGRADATION: frozenset({RemediationAction.SWITCH_SOURCE}),
    FaultClass.ENCODER_FAILURE: frozenset({
        RemediationAction.RESTART_ENCODER,
        RemediationAction.SWITCH_SOURCE,
        RemediationAction.FAILOVER,
    }),
    FaultClass.UNKNOWN: frozenset(),
}

APPROVED_TARGETS = {
    FaultClass.ENCODER_OVERLOAD: frozenset({"encoder_01"}),
    FaultClass.ENCODER_FAILURE: frozenset({"encoder_01"}),
    FaultClass.NETWORK_DEGRADATION: frozenset({"network path"}),
    FaultClass.UNKNOWN: frozenset(),
}

ACTION_BLAST_RADIUS = {
    RemediationAction.RESTART_ENCODER: BlastRadius.COMPONENT,
    RemediationAction.REDUCE_PROFILE: BlastRadius.PROFILE,
    RemediationAction.SWITCH_SOURCE: BlastRadius.STREAM,
    RemediationAction.FAILOVER: BlastRadius.STREAM,
}

_BLAST_RADIUS_NAMES = {
    "component": BlastRadius.COMPONENT,
    "profile": BlastRadius.PROFILE,
    "stream": BlastRadius.STREAM,
}


@dataclass(frozen=True)
class SafetyConfig:
    min_confidence: float
    max_actions: int
    cooldown_seconds: float
    max_blast_radius: BlastRadius

    @classmethod
    def from_env(cls) -> "SafetyConfig":
        min_confidence = float(os.environ.get("SAFETY_MIN_CONFIDENCE", "0.80"))
        max_actions = int(os.environ.get("SAFETY_MAX_ACTIONS", "2"))
        cooldown_seconds = float(os.environ.get("SAFETY_COOLDOWN_SECONDS", "60"))
        radius_name = os.environ.get("SAFETY_MAX_BLAST_RADIUS", "stream").strip().lower()

        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("SAFETY_MIN_CONFIDENCE must be between 0 and 1")
        if max_actions < 1:
            raise ValueError("SAFETY_MAX_ACTIONS must be at least 1")
        if cooldown_seconds < 0:
            raise ValueError("SAFETY_COOLDOWN_SECONDS must be non-negative")
        if radius_name not in _BLAST_RADIUS_NAMES:
            raise ValueError("SAFETY_MAX_BLAST_RADIUS must be component, profile, or stream")
        return cls(min_confidence, max_actions, cooldown_seconds, _BLAST_RADIUS_NAMES[radius_name])


def _pass(name: str, detail: str) -> SafetyCheck:
    return SafetyCheck(name=name, passed=True, detail=detail)


def _fail(name: str, detail: str) -> SafetyCheck:
    return SafetyCheck(name=name, passed=False, detail=detail)


def _check_allow_list(proposal: RemediationProposal) -> SafetyCheck:
    allowed = frozenset(RemediationAction)
    if proposal.action not in allowed:
        return _fail("allow_list", f"action {proposal.action!r} is not permitted")
    return _pass("allow_list", f"{proposal.action.value} is explicitly permitted")


def _check_incident_match(incident: Incident, proposal: RemediationProposal) -> SafetyCheck:
    evidence = incident.evidence
    if proposal.incident_id != incident.incident_id:
        return _fail("incident_match", "proposal incident_id does not match loaded incident")
    if evidence is None or evidence.incident_id != incident.incident_id:
        return _fail("incident_match", "evidence incident_id does not match loaded incident")
    return _pass("incident_match", "proposal, evidence, and incident IDs match")


def _check_compatibility(
    proposal: RemediationProposal, evidence: IncidentEvidence
) -> SafetyCheck:
    compatible = ACTION_COMPATIBILITY.get(evidence.fault_class)
    if compatible is None or proposal.action not in compatible:
        return _fail(
            "compatibility",
            f"{proposal.action.value} is not approved for {evidence.fault_class.value}",
        )
    return _pass(
        "compatibility",
        f"{proposal.action.value} is approved for {evidence.fault_class.value}",
    )


def _check_approved_target(evidence: IncidentEvidence) -> SafetyCheck:
    target = evidence.infra.affected_component.strip().lower()
    approved = APPROVED_TARGETS.get(evidence.fault_class, frozenset())
    if target not in approved:
        return _fail(
            "approved_target",
            f"target {evidence.infra.affected_component!r} is not approved for "
            f"{evidence.fault_class.value}",
        )
    return _pass("approved_target", f"target {target!r} is approved")


def _check_evidence_and_confidence(
    proposal: RemediationProposal, evidence: IncidentEvidence, config: SafetyConfig
) -> SafetyCheck:
    if not evidence.validation_passed:
        return _fail("evidence_confidence", "aggregated evidence did not pass validation")
    if evidence.validation_errors:
        return _fail("evidence_confidence", "aggregated evidence contains validation errors")
    if evidence.fault_class is FaultClass.UNKNOWN:
        return _fail("evidence_confidence", "fault class is unknown")
    if evidence.confidence < config.min_confidence:
        return _fail(
            "evidence_confidence",
            f"evidence confidence {evidence.confidence:.3f} is below {config.min_confidence:.3f}",
        )
    if proposal.confidence < config.min_confidence:
        return _fail(
            "evidence_confidence",
            f"proposal confidence {proposal.confidence:.3f} is below {config.min_confidence:.3f}",
        )
    return _pass(
        "evidence_confidence",
        f"evidence {evidence.confidence:.3f} and proposal {proposal.confidence:.3f} "
        f"meet {config.min_confidence:.3f}",
    )


def _check_blast_radius(proposal: RemediationProposal, config: SafetyConfig) -> SafetyCheck:
    actual = ACTION_BLAST_RADIUS.get(proposal.action)
    if actual is None or actual > config.max_blast_radius:
        actual_name = actual.name.lower() if actual is not None else "unknown"
        return _fail(
            "blast_radius",
            f"action radius {actual_name} exceeds allowed {config.max_blast_radius.name.lower()}",
        )
    return _pass(
        "blast_radius",
        f"action radius {actual.name.lower()} is within {config.max_blast_radius.name.lower()}",
    )


def _check_action_budget(incident: Incident, config: SafetyConfig) -> SafetyCheck:
    if incident.attempt_count != len(incident.actions_attempted):
        return _fail("action_budget", "attempt_count and actions_attempted are inconsistent")
    if incident.attempt_count >= config.max_actions:
        return _fail(
            "action_budget",
            f"{incident.attempt_count} actions already attempted; maximum is {config.max_actions}",
        )
    return _pass(
        "action_budget",
        f"{incident.attempt_count} of {config.max_actions} actions attempted",
    )


def _check_cooldown(
    incident: Incident, config: SafetyConfig, now: datetime
) -> SafetyCheck:
    if incident.attempt_count == 0:
        return _pass("cooldown", "no previous action")
    previous = incident.updated_at
    if previous.tzinfo is None or previous.utcoffset() is None:
        return _fail("cooldown", "last-action timestamp is not timezone-aware")
    elapsed = (now - previous).total_seconds()
    if elapsed < 0:
        return _fail("cooldown", "last-action timestamp is in the future")
    if elapsed < config.cooldown_seconds:
        return _fail(
            "cooldown",
            f"only {elapsed:.1f}s elapsed; requires {config.cooldown_seconds:.1f}s",
        )
    return _pass(
        "cooldown",
        f"{elapsed:.1f}s elapsed; requires {config.cooldown_seconds:.1f}s",
    )


def _idempotency_key(incident: Incident, action: RemediationAction) -> str:
    material = f"{incident.incident_id}:{action.value}:{incident.attempt_count + 1}"
    return "safety-" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _blocked(
    incident: Incident,
    action: RemediationAction,
    checks: list[SafetyCheck],
    reason: str,
) -> SafetyDecision:
    return SafetyDecision(
        incident_id=incident.incident_id,
        action=action,
        verdict=SafetyVerdict.BLOCK,
        checks=checks,
        block_reason=reason,
        idempotency_key=_idempotency_key(incident, action),
    )


def evaluate_safety(
    incident: Incident,
    *,
    config: SafetyConfig | None = None,
    now: datetime | None = None,
) -> SafetyDecision:
    """Return ALLOW only if every deterministic policy check explicitly passes."""
    checks: list[SafetyCheck] = []
    # SafetyDecision currently requires an enum action even for malformed input.
    # This placeholder grants no authority because every such path is BLOCK.
    action = RemediationAction.RESTART_ENCODER
    try:
        proposal = incident.proposal
        if proposal is None:
            check = _fail("proposal", "remediation proposal is missing")
            return _blocked(incident, action, [check], f"{check.name}: {check.detail}")
        action = proposal.action
        evidence = incident.evidence
        if evidence is None:
            check = _fail("evidence_confidence", "aggregated evidence is missing")
            return _blocked(incident, action, [check], f"{check.name}: {check.detail}")

        active_config = config if config is not None else SafetyConfig.from_env()
        evaluated_at = now if now is not None else datetime.now(timezone.utc)
        check_functions: tuple[Callable[[], SafetyCheck], ...] = (
            lambda: _check_allow_list(proposal),
            lambda: _check_incident_match(incident, proposal),
            lambda: _check_compatibility(proposal, evidence),
            lambda: _check_approved_target(evidence),
            lambda: _check_evidence_and_confidence(proposal, evidence, active_config),
            lambda: _check_blast_radius(proposal, active_config),
            lambda: _check_action_budget(incident, active_config),
            lambda: _check_cooldown(incident, active_config, evaluated_at),
        )
        for run_check in check_functions:
            try:
                check = run_check()
            except Exception as exc:
                check = _fail("check_error", f"{type(exc).__name__}: {exc}")
            checks.append(check)
            if not check.passed:
                return _blocked(incident, action, checks, f"{check.name}: {check.detail}")

        return SafetyDecision(
            incident_id=incident.incident_id,
            action=action,
            verdict=SafetyVerdict.ALLOW,
            checks=checks,
            block_reason=None,
            idempotency_key=_idempotency_key(incident, action),
        )
    except Exception as exc:
        check = _fail("gate_error", f"{type(exc).__name__}: {exc}")
        checks.append(check)
        return _blocked(incident, action, checks, f"{check.name}: {check.detail}")


def run_for_incident(incident_id: str) -> SafetyDecision:
    """Load, evaluate, and durably store one incident's gate decision."""
    recorder = IncidentRecorder()
    incident = recorder.load(incident_id)
    incident.status = IncidentStatus.GATING
    incident.current_step = "gate"
    recorder.save(incident)

    decision = evaluate_safety(incident)
    incident.safety_decision = decision
    incident.idempotency_key = decision.idempotency_key
    recorder.save(incident)
    return decision


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("usage: python3 safety_gate.py <incident_id>")
    try:
        result = run_for_incident(sys.argv[1])
    except KeyError:
        sys.exit(f"ERROR: no incident {sys.argv[1]!r} in Firestore")
    except Exception as exc:
        sys.exit(f"ERROR: could not persist safety decision: {exc}")

    print(result.model_dump_json(indent=2))
    if result.verdict is SafetyVerdict.BLOCK:
        sys.exit(1)
