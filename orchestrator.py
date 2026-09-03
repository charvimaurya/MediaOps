"""Durable coordinator for the real MediaOps incident lifecycle.

The orchestrator owns sequencing only. Safety Gate owns authorization, Control
Plane owns execution, and Verify Recovery owns the recovery verdict.

    python3 orchestrator.py <incident_id>
"""

from __future__ import annotations

import logging
import json
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Callable

from aggregator import AggregationError, aggregate
from control_plane import execute_incident
from fallback import FallbackStopped, run_fallback
from incident_recorder import IncidentRecorder
from agents.infra_agent import analyze_infra
from kb_writeback import run_writeback
from knowledge_base import retrieve
from models import (
    Incident,
    IncidentStatus,
    ExecutionResult,
    InfraFinding,
    SafetyDecision,
    SafetyVerdict,
    VerificationResult,
    VerificationVerdict,
    VisionFinding,
)
from agents.remediation_agent import propose_remediation
from report import run_report
from observability import log_event, record_event
from safety_gate import SafetyConfig, run_for_incident as run_safety_gate
from verify_recovery import run_for_incident as run_verification
from agents.vision_agent import analyze_frame


logger = logging.getLogger("orchestrator")

SIMULATOR_STATE_URL = os.environ.get(
    "SIMULATOR_STATE_URL", "http://localhost:8001/state"
)
SIMULATOR_STATE_TIMEOUT_SECONDS = float(
    os.environ.get("SIMULATOR_STATE_TIMEOUT_SECONDS", "5")
)

ACTIVE_FAULT_VIDEO_SECTION = {
    "encoder_overload": "overload",
    "encoder_failure": "encoder_failure",
    "network_degradation": "healthy",
    "healthy": "healthy",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def read_active_video_section(
    *,
    state_url: str = SIMULATOR_STATE_URL,
    timeout_seconds: float = SIMULATOR_STATE_TIMEOUT_SECONDS,
    opener=urllib.request.urlopen,
) -> str:
    """Map the simulator's read-only active fault state to a video section."""
    if not state_url or timeout_seconds <= 0:
        raise ValueError("simulator state URL and positive timeout are required")
    with opener(state_url, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("simulator /state response must be a JSON object")
    required_health = ("fps", "dropped_frames", "packet_loss", "encoder_status")
    missing = [name for name in required_health if name not in payload]
    if missing:
        raise ValueError(
            f"simulator /state response is missing: {', '.join(missing)}"
        )
    try:
        healthy = (
            int(payload["encoder_status"]) == 1
            and float(payload["fps"]) >= 24
            and float(payload["dropped_frames"]) < 5
            and float(payload["packet_loss"]) < 5
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("simulator /state health fields are malformed") from exc
    if healthy:
        return "healthy"

    failure_mode = payload.get("failure_mode")
    if not isinstance(failure_mode, str) or not failure_mode:
        raise ValueError("simulator /state response has no valid failure_mode")
    section = ACTIVE_FAULT_VIDEO_SECTION.get(failure_mode)
    if section is None:
        raise ValueError(f"unsupported active failure_mode {failure_mode!r}")
    return section


class Orchestrator:
    """Coordinate real components while keeping Firestore authoritative."""

    def __init__(
        self,
        recorder: IncidentRecorder | None = None,
        *,
        vision_runner: Callable[..., VisionFinding | None] = analyze_frame,
        infra_runner: Callable[[Incident], InfraFinding | None] = analyze_infra,
        retrieve_runner: Callable[[object], list] = retrieve,
        remediation_runner: Callable[[object, list], object] = propose_remediation,
        gate_runner: Callable[[str], SafetyDecision] | None = None,
        control_runner: Callable[[str], ExecutionResult] | None = None,
        verify_runner: Callable[[str], VerificationResult] | None = None,
        fallback_runner: Callable[[str], VerificationResult] | None = None,
        report_runner: Callable[[str], object] | None = None,
        writeback_runner: Callable[[str], object] | None = None,
        active_section_reader: Callable[[], str] = read_active_video_section,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        self._recorder = recorder or IncidentRecorder()
        self._vision = vision_runner
        self._infra = infra_runner
        self._retrieve = retrieve_runner
        self._remediate = remediation_runner
        self._gate = gate_runner or (
            lambda incident_id: run_safety_gate(
                incident_id, recorder=self._recorder
            )
        )
        self._control = control_runner or (
            lambda incident_id: execute_incident(
                incident_id, recorder=self._recorder
            )
        )
        self._verify = verify_runner or (
            lambda incident_id: run_verification(
                incident_id, recorder=self._recorder
            )
        )
        self._fallback = fallback_runner or (
            lambda incident_id: run_fallback(
                incident_id,
                recorder=self._recorder,
                gate_runner=lambda value: run_safety_gate(
                    value, recorder=self._recorder
                ),
                control_runner=lambda value: execute_incident(
                    value, recorder=self._recorder
                ),
                verify_runner=lambda value: run_verification(
                    value, recorder=self._recorder
                ),
            )
        )
        self._report = report_runner or (
            lambda incident_id: run_report(
                incident_id, recorder=self._recorder
            )
        )
        self._writeback = writeback_runner or (
            lambda incident_id: run_writeback(
                incident_id, recorder=self._recorder
            )
        )
        self._active_section = active_section_reader
        self._sleep = sleeper

    def run(self, incident_id: str) -> Incident:
        incident = self._recorder.load(incident_id)
        final_stops = {
            IncidentStatus.BLOCKED,
            IncidentStatus.CANNOT_VERIFY,
            IncidentStatus.AUTOMATION_FAILED,
            IncidentStatus.ESCALATED,
            IncidentStatus.FAILED,
            IncidentStatus.CLOSED,
        }
        if incident.status in final_stops:
            log_event("orchestrator", incident_id, incident.current_step, "already_terminal", detail=incident.status.value)
            return incident
        print(f"\n=== orchestrating {incident_id} ===")

        try:
            self._enter(incident, IncidentStatus.DIAGNOSING, "diagnose")
            vision, infra = self._parallel_diagnosis(incident)
            incident.vision = vision
            incident.infra = infra
            # record_event builds the durable timeline; save() commits these
            # independent witness results and their breadcrumbs to Firestore.
            record_event(incident, "vision", "diagnose", "completed" if vision else "no_finding",
                         detail=vision.symptom.value if vision else "Vision returned no finding")
            record_event(incident, "infra", "diagnose", "completed" if infra else "no_finding",
                         detail=infra.fault_class.value if infra else "Infra returned no finding")
            self._recorder.save(incident)

            self._enter(incident, IncidentStatus.AGGREGATING, "aggregate")
            try:
                incident.evidence = aggregate(incident.incident_id, vision, infra)
            except AggregationError as exc:
                return self._terminal(
                    incident, IncidentStatus.ESCALATED, "aggregate", str(exc)
                )
            self._recorder.save(incident)
            record_event(incident, "aggregator", "aggregate", "completed",
                         detail=f"agreement={incident.evidence.agreement}")
            self._recorder.save(incident)

            self._enter(incident, IncidentStatus.RETRIEVING, "retrieve")
            incident.precedent = self._retrieve(incident.evidence)
            record_event(incident, "knowledge_base", "retrieve", "completed",
                         detail=f"matches={len(incident.precedent)}")
            self._recorder.save(incident)

            self._enter(incident, IncidentStatus.DECIDING, "decide")
            incident.proposal = self._remediate(
                incident.evidence, incident.precedent
            )
            if incident.proposal is None:
                raise RuntimeError("Remediation Agent returned no valid proposal")
            record_event(incident, "remediation", "propose", "completed",
                         action=incident.proposal.action, detail=incident.proposal.rationale)
            self._recorder.save(incident)

            self._enter(incident, IncidentStatus.GATING, "gate")
            decision = self._gate(incident_id)
            incident = self._recorder.load(incident_id)
            record_event(incident, "safety_gate", "gate", decision.verdict.value.lower(),
                         action=decision.action, detail=decision.block_reason or "all checks passed")
            self._recorder.save(incident)
            if decision.verdict is not SafetyVerdict.ALLOW:
                return self._terminal(
                    incident,
                    IncidentStatus.BLOCKED,
                    "gate",
                    decision.block_reason or "Safety Gate blocked without a reason",
                )

            self._enter(incident, IncidentStatus.EXECUTING, "execute")
            execution = self._control(incident_id)
            incident = self._recorder.load(incident_id)
            record_event(incident, "control_plane", "execute", "succeeded" if execution.success else "failed",
                         action=execution.action, detail=execution.detail)
            self._recorder.save(incident)
            if not execution.success:
                incident.automation_failure_reason = execution.detail
                return self._terminal(
                    incident,
                    IncidentStatus.AUTOMATION_FAILED,
                    "execute",
                    execution.detail,
                )

            self._enter(incident, IncidentStatus.VERIFYING, "verify")
            verification = self._verify(incident_id)
            incident = self._recorder.load(incident_id)
            record_event(incident, "verify", "verify", verification.verdict.value.lower(),
                         action=verification.action, detail="; ".join(verification.failed_checks) or "both domains healthy")
            self._recorder.save(incident)
            if verification.verdict is VerificationVerdict.CANNOT_VERIFY:
                return self._terminal(
                    incident,
                    IncidentStatus.CANNOT_VERIFY,
                    "verify",
                    "; ".join(verification.failed_checks)
                    or "verification unavailable",
                )
            if verification.verdict is VerificationVerdict.RECOVERY_FAILED:
                incident = self._wait_for_fallback_cooldown(incident)
                try:
                    verification = self._fallback(incident_id)
                except FallbackStopped:
                    return self._recorder.load(incident_id)
                incident = self._recorder.load(incident_id)

            if verification.verdict is not VerificationVerdict.RECOVERED:
                raise RuntimeError(
                    f"unhandled verification verdict {verification.verdict.value}"
                )

            incident.status = IncidentStatus.RECOVERED
            incident.current_step = "report"
            incident.notes.append("recovery positively verified in both domains")
            record_event(incident, "orchestrator", "recovered", "completed", status=IncidentStatus.RECOVERED)
            self._recorder.save(incident)

            self._report(incident_id)
            incident = self._recorder.load(incident_id)
            record_event(incident, "report", "report", "sent" if incident.report_sent else "not_sent",
                         detail=incident.report_error or "Slack report sent")
            incident.current_step = "kb_writeback"
            self._recorder.save(incident)
            self._writeback(incident_id)

            incident = self._recorder.load(incident_id)
            record_event(incident, "kb_writeback", "kb_writeback",
                         "written" if incident.kb_writeback_id else "not_written",
                         detail=incident.kb_writeback_error or incident.kb_writeback_id or "")
            incident.status = IncidentStatus.CLOSED
            incident.current_step = "close"
            incident.closed_at = _utcnow()
            incident.notes.append("incident lifecycle closed")
            incident.terminal_step = None
            incident.terminal_reason = None
            record_event(incident, "orchestrator", "close", "closed", status=IncidentStatus.CLOSED)
            self._recorder.save(incident)
            print(f"=== incident {incident_id} CLOSED ===")
            return incident
        except Exception as exc:
            incident = self._recorder.load(incident_id)
            self._fail_closed(incident, exc)
            raise

    def _parallel_diagnosis(
        self, incident: Incident
    ) -> tuple[VisionFinding | None, InfraFinding | None]:
        # Read ground-truth stream state before starting either worker. Vision
        # receives only a section selection; Infra independently classifies
        # bounded metrics and never supplies Vision's answer.
        section = self._active_section()
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="diagnosis") as pool:
            vision_future = pool.submit(
                self._vision, incident.model_copy(deep=True), fault=section
            )
            infra_future = pool.submit(
                self._infra, incident.model_copy(deep=True)
            )
            return vision_future.result(), infra_future.result()

    def _wait_for_fallback_cooldown(self, incident: Incident) -> Incident:
        if incident.execution is None:
            return incident
        cooldown = SafetyConfig.from_env().cooldown_seconds
        elapsed = (_utcnow() - incident.execution.executed_at).total_seconds()
        remaining = max(0.0, cooldown - elapsed)
        incident.current_step = "fallback_wait"
        incident.notes.append(
            f"recovery failed; waiting {remaining:.1f}s for "
            f"{cooldown:.1f}s action cooldown"
        )
        record_event(incident, "orchestrator", "fallback_wait", "waiting",
                     detail=f"cooldown remaining={remaining:.1f}s")
        self._recorder.save(incident)
        if remaining:
            self._sleep(remaining)
        return self._recorder.load(incident.incident_id)

    def _enter(self, incident: Incident, status: IncidentStatus, step: str) -> None:
        incident.status = status
        incident.current_step = step
        # Persist the transition before doing step work, so an interrupted run
        # still shows exactly where orchestration stopped.
        record_event(incident, "orchestrator", step, "started", status=status)
        self._recorder.save(incident)
        print(f"-> {status.value}")

    def _terminal(
        self,
        incident: Incident,
        status: IncidentStatus,
        step: str,
        reason: str,
    ) -> Incident:
        incident.status = status
        incident.current_step = step
        incident.closed_at = _utcnow()
        incident.terminal_step = step
        incident.terminal_reason = reason
        incident.notes.append(f"{status.value} during {step}: {reason}")
        # Terminal diagnostics are first-class Firestore fields and a timeline
        # event, rather than being available only in ephemeral process logs.
        record_event(incident, "orchestrator", step, status.value.lower(), status=status, detail=reason)
        self._recorder.save(incident)
        print(f"-> {status.value}: {reason}")
        return incident

    def _fail_closed(self, incident: Incident, exc: Exception) -> None:
        failed_step = incident.current_step
        incident.status = IncidentStatus.FAILED
        incident.current_step = "failed"
        incident.closed_at = _utcnow()
        incident.terminal_step = failed_step
        incident.terminal_reason = f"{type(exc).__name__}: {exc}"
        incident.notes.append(
            f"FAILED during {failed_step}: {type(exc).__name__}: {exc}"
        )
        # Fail-closed errors retain both the failed step and human-readable cause
        # so trace.py can explain the stop without access to the original console.
        record_event(incident, "orchestrator", failed_step, "failed",
                     status=IncidentStatus.FAILED, detail=incident.terminal_reason)
        self._recorder.save(incident)
        logger.error(
            "incident %s failed during %s: %s",
            incident.incident_id,
            failed_step,
            exc,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    if len(sys.argv) != 2:
        sys.exit("usage: python3 orchestrator.py <incident_id>")
    try:
        final = Orchestrator().run(sys.argv[1])
    except KeyError:
        sys.exit(f"ERROR: no incident {sys.argv[1]!r} in Firestore")
    except Exception as exc:
        sys.exit(f"FAILED: {type(exc).__name__}: {exc}")
    print(final.model_dump_json(indent=2))
    if final.status is not IncidentStatus.CLOSED:
        sys.exit(1)
