"""
The Orchestrator (skeleton) -- coordinates the whole incident lifecycle.

It takes an incident_id (from the Incident Recorder), loads that Incident from
Firestore, and drives it through:

    DIAGNOSING -> AGGREGATING -> RETRIEVING -> DECIDING -> GATING
               -> EXECUTING -> VERIFYING -> RESOLVED

persisting the Incident to Firestore on entering each phase AND again after that
phase's result is attached.

Every step here is a FAKE stub returning a correctly-shaped object from models.py
with obviously-fake values. We are testing the FLOW and the GUARDRAILS, not the
real logic -- stubs get swapped for real components one at a time (CLAUDE.md).

Fail-closed: if any stub raises (including one running in the parallel
vision/infra branch), the incident is marked FAILED in Firestore, later steps do
not run, and run() re-raises.

    python3 orchestrator.py [incident_id]
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from incident_recorder import IncidentRecorder
from models import (
    AnomalyEvent,
    FaultClass,
    Incident,
    IncidentEvidence,
    IncidentStatus,
    InfraFinding,
    RemediationAction,
    RemediationProposal,
    SafetyCheck,
    SafetyDecision,
    SafetyVerdict,
    Severity,
    VerificationResult,
    VisionFinding,
    VisionSymptom,
)

logger = logging.getLogger("orchestrator")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# Observability aid: the vision/infra stubs record which thread they ran on, so
# a test can prove the two ran concurrently on distinct worker threads.
VISION_INFRA_THREADS: list[str] = []

# Test hook: seconds the vision/infra stubs linger, so a test can force the two
# to actually overlap (a ThreadPoolExecutor spawns a 2nd thread only if the 1st
# is still busy). 0.0 in normal use -- no effect on the real run or __main__.
STUB_DELAY_SECONDS = 0.0


# --------------------------------------------------------------------------- #
# The nine FAKE stubs. Each raises when `fail=True`; otherwise returns a
# schema-valid models.py object. "Fakeness" lives in the free-text fields --
# enum fields must be real enum members or StrictModel rejects them.
# --------------------------------------------------------------------------- #

def fake_vision(fail: bool = False) -> VisionFinding:
    VISION_INFRA_THREADS.append(threading.current_thread().name)
    time.sleep(STUB_DELAY_SECONDS)
    if fail:
        raise RuntimeError("forced failure in fake_vision")
    return VisionFinding(
        frame_captured_at=_utcnow(),
        symptom=VisionSymptom.BLACK_FRAME,
        description="FAKE vision finding: frame looks black (stub, no real frame read)",
        confidence=0.99,
        model="fake-vision-stub",
        raw_response="FAKE raw gemini response",
    )


def fake_infra(fail: bool = False) -> InfraFinding:
    VISION_INFRA_THREADS.append(threading.current_thread().name)
    time.sleep(STUB_DELAY_SECONDS)
    if fail:
        raise RuntimeError("forced failure in fake_infra")
    return InfraFinding(
        fault_class=FaultClass.ENCODER_OVERLOAD,
        affected_component="encoder_01",
        description="FAKE infra finding: cpu pinned, fps low (stub, no real telemetry read)",
        supporting_metrics={"media_cpu_usage_percent": 99.0, "media_fps": 12.0},
        confidence=0.98,
        model="fake-infra-stub",
        raw_response="FAKE raw gemini response",
    )


def fake_aggregate(
    incident_id: str, vision: VisionFinding, infra: InfraFinding, fail: bool = False
) -> IncidentEvidence:
    if fail:
        raise RuntimeError("forced failure in fake_aggregate")
    return IncidentEvidence(
        incident_id=incident_id,
        vision=vision,
        infra=infra,
        agreement=True,
        summary="FAKE aggregated evidence: stub vision + stub infra combined for flow testing",
        validation_passed=True,
        validation_errors=[],
    )


def fake_retrieve(evidence: IncidentEvidence, fail: bool = False) -> list[str]:
    if fail:
        raise RuntimeError("forced failure in fake_retrieve")
    return ["FAKE-hist-0001", "FAKE-hist-0002"]


def fake_remediate(
    incident_id: str, evidence: IncidentEvidence, similar_ids: list[str], fail: bool = False
) -> RemediationProposal:
    if fail:
        raise RuntimeError("forced failure in fake_remediate")
    return RemediationProposal(
        incident_id=incident_id,
        action=RemediationAction.RESTART_ENCODER,
        rationale="FAKE rationale: restart clears the (stub) encoder fault",
        confidence=0.97,
        model="fake-remediation-stub",
        similar_incident_ids=similar_ids,
        precedent_summary="FAKE precedent: 2 similar stub incidents recovered via restart",
        raw_response="FAKE raw gemini response",
    )


def fake_safety_gate(
    incident_id: str, proposal: RemediationProposal, fail: bool = False
) -> SafetyDecision:
    if fail:
        raise RuntimeError("forced failure in fake_safety_gate")
    return SafetyDecision(
        incident_id=incident_id,
        action=proposal.action,
        verdict=SafetyVerdict.ALLOW,
        checks=[
            SafetyCheck(name="allow_list", passed=True, detail="FAKE: action is on the stub allow-list"),
            SafetyCheck(name="confidence", passed=True, detail="FAKE: 0.97 >= threshold"),
            SafetyCheck(name="cooldown", passed=True, detail="FAKE: no recent action"),
        ],
        block_reason=None,
        idempotency_key=f"FAKE-{incident_id}-{proposal.action.value}",
    )


def fake_execute(
    proposal: RemediationProposal, decision: SafetyDecision, fail: bool = False
) -> dict:
    if fail:
        raise RuntimeError("forced failure in fake_execute")
    return {
        "action": proposal.action.value,
        "ok": True,
        "detail": "FAKE execute -- no real control.py call was made",
        "idempotency_key": decision.idempotency_key,
    }


def fake_verify(
    incident_id: str, proposal: RemediationProposal, fail: bool = False
) -> VerificationResult:
    if fail:
        raise RuntimeError("forced failure in fake_verify")
    return VerificationResult(
        incident_id=incident_id,
        action=proposal.action,
        recovered=True,
        telemetry_ok=True,
        video_ok=True,
        health_value=1,
        stable_window_seconds=20.0,
        samples=[{"media_pipeline_health": 1.0}, {"media_pipeline_health": 1.0}],
        failed_checks=[],
        vision_recheck=None,
    )


def fake_report(incident: Incident, fail: bool = False) -> str:
    if fail:
        raise RuntimeError("forced failure in fake_report")
    action = incident.proposal.action.value if incident.proposal else "?"
    return f"FAKE Slack report -- incident {incident.incident_id[:8]} resolved by {action} (stub)"


# --------------------------------------------------------------------------- #
# The Orchestrator
# --------------------------------------------------------------------------- #

class Orchestrator:
    """Drives one incident through the whole lifecycle, persisting after each step."""

    def __init__(self, recorder: IncidentRecorder | None = None) -> None:
        self._recorder = recorder or IncidentRecorder()

    def run(self, incident_id: str, *, fail_at: str | None = None) -> Incident:
        incident = self._recorder.load(incident_id)
        print(f"\n=== orchestrating incident {incident_id} (status={incident.status.value}) ===")

        try:
            # 1. DIAGNOSING -- vision || infra concurrently
            self._enter(incident, IncidentStatus.DIAGNOSING, "diagnose")
            vision, infra = self._parallel_vision_infra(fail_at)
            print(f"   vision.symptom={vision.symptom.value}  infra.fault_class={infra.fault_class.value}")
            self._recorder.save(incident)

            # 2. AGGREGATING. 

            self._enter(incident, IncidentStatus.AGGREGATING, "aggregate")
            incident.evidence = fake_aggregate(
                incident.incident_id, vision, infra, fail=(fail_at == "aggregate")
            )
            print(f"   evidence.summary={incident.evidence.summary!r}")
            self._recorder.save(incident)

            # 3. RETRIEVING
            self._enter(incident, IncidentStatus.RETRIEVING, "retrieve")
            similar_ids = fake_retrieve(incident.evidence, fail=(fail_at == "retrieve"))
            incident.notes.append(f"FAKE retrieve: {similar_ids}")
            print(f"   similar_incident_ids={similar_ids}")
            self._recorder.save(incident)

            # 4. DECIDING - remidiation 
            self._enter(incident, IncidentStatus.DECIDING, "decide")
            incident.proposal = fake_remediate(
                incident.incident_id, incident.evidence, similar_ids, fail=(fail_at == "remediate")
            )
            print(f"   proposal.action={incident.proposal.action.value}  (model={incident.proposal.model})")
            self._recorder.save(incident)

            # 5. GATING - safety gate 
            self._enter(incident, IncidentStatus.GATING, "gate")
            decision = fake_safety_gate(
                incident.incident_id, incident.proposal, fail=(fail_at == "safety_gate")
            )
            incident.safety_decision = decision
            incident.idempotency_key = decision.idempotency_key
            if decision.verdict is not SafetyVerdict.ALLOW:
                raise RuntimeError(f"safety gate BLOCKED: {decision.block_reason}")
            print(f"   safety verdict={decision.verdict.value}  idempotency_key={decision.idempotency_key}")
            self._recorder.save(incident)

            # 6. EXECUTING 
            self._enter(incident, IncidentStatus.EXECUTING, "execute")
            result = fake_execute(incident.proposal, decision, fail=(fail_at == "execute"))
            incident.actions_attempted.append(incident.proposal.action)
            incident.attempt_count += 1
            incident.notes.append(f"FAKE execute: {result}")
            print(f"   executed {result['action']}  ok={result['ok']}")
            self._recorder.save(incident)

            # 7. VERIFYING
            self._enter(incident, IncidentStatus.VERIFYING, "verify")
            verification = fake_verify(
                incident.incident_id, incident.proposal, fail=(fail_at == "verify")
            )
            incident.verification = verification
            if not verification.recovered:
                raise RuntimeError("verification says the incident is NOT recovered")
            print(f"   verified recovered={verification.recovered}  health_value={verification.health_value}")
            self._recorder.save(incident)

            # 8. RESOLVED -- report, then close
            report = fake_report(incident, fail=(fail_at == "report"))
            incident.notes.append(f"FAKE report: {report}")
            incident.report_sent = True
            print(f"   {report}")
            self._enter(incident, IncidentStatus.RESOLVED, "resolve")
            incident.closed_at = _utcnow()
            self._recorder.save(incident)

            print(f"=== incident {incident_id} RESOLVED ===\n")

        except Exception as exc:
            self._fail_closed(incident, exc)
            raise

        return incident

    # -- helpers ---------------------------------------------------------- #

    def _enter(self, incident: Incident, status: IncidentStatus, step: str) -> None:
        incident.status = status
        incident.current_step = step
        print(f"-> {status.value}")
        self._recorder.save(incident)

    def _parallel_vision_infra(self, fail_at: str | None) -> tuple[VisionFinding, InfraFinding]:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="agent") as pool:
            fv = pool.submit(fake_vision, fail=(fail_at == "vision"))
            fi = pool.submit(fake_infra, fail=(fail_at == "infra"))
            vision = fv.result()  # re-raises if the worker raised
            infra = fi.result()
        return vision, infra

    def _fail_closed(self, incident: Incident, exc: Exception) -> None:
        failed_step = incident.current_step
        logger.error("workflow failed during %r: %r", failed_step, exc)
        try:
            incident.status = IncidentStatus.FAILED
            incident.current_step = "failed"
            incident.notes.append(f"FAILED during {failed_step}: {exc!r}")
            incident.closed_at = _utcnow()
            self._recorder.save(incident)
            print(f"✗ incident {incident.incident_id} marked FAILED in Firestore (failed during {failed_step})\n")
        except Exception:
            logger.exception("could not persist FAILED status -- Firestore write also failed")
            raise


# --------------------------------------------------------------------------- #
# Manual end-to-end run
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) > 1:
        incident_id = sys.argv[1]
    else:
        seed = AnomalyEvent(
            fault_class=FaultClass.UNKNOWN,
            severity=Severity.HIGH,
            reason="FAKE seed anomaly for the orchestrator skeleton demo",
            health_value=0,
            telemetry_snapshot={"media_fps": 18.0},
            breach_count=6,
        )
        incident_id = IncidentRecorder().record(seed)
        print(f"seeded incident {incident_id}")

    final = Orchestrator().run(incident_id)
    print(final.model_dump_json(indent=2))
