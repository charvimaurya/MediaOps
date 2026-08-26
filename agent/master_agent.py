"""
Deterministic state machine that drives an incident from RECEIVED through
diagnose -> decide -> guardrail-check -> act -> verify -> resolve or
fail-safe. No AI here -- DiagnosisProvider and ActionExecutor are
pluggable seams (stub implementations for now).

No human-approval state anywhere: Incident -> Master Agent -> Decision ->
Guardrails -> Action. Safety comes from GUARDRAIL_CHECK re-validating
every chosen action against the policy table, not from a human in the
loop.
"""

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from detector.models import Incident, IncidentStatus
from agent.aht import AHTTimer, EscalationPolicy
from agent import policy
from agent.diagnosis import DiagnosisProvider, Diagnosis
from agent.knowledge import KnowledgeBase
from agent.remediation import ActionExecutor
from agent.verification import Verifier
from agent.timeline import Timeline
from agent.guardrails import GuardrailEngine
from agent.recovery_metrics import compute_rto, compute_rpo, RPOResult
from agent.metrics import AHT_SECONDS, RTO_SECONDS, RPO_SECONDS, AGENT_ACTIONS_TOTAL, ESCALATION_PATH

# Only imported for the type hint -- kept optional so tests/existing
# callers that don't care about RPO don't need to construct one.
from simulator.output_accounting import OutputAccounting

logger = logging.getLogger(__name__)

# Backstop only -- MAX_ATTEMPTS (policy.py) should always terminate the
# decide/act/verify loop well before this many iterations.
HARD_ITERATION_CAP = 10


class AgentState(str, Enum):
    RECEIVED = "RECEIVED"
    DIAGNOSING = "DIAGNOSING"
    CONSULTING_KNOWLEDGE = "CONSULTING_KNOWLEDGE"
    DECIDING = "DECIDING"
    GUARDRAIL_CHECK = "GUARDRAIL_CHECK"
    ACTING = "ACTING"
    VERIFYING = "VERIFYING"
    RESOLVED = "RESOLVED"
    FAILED_SAFE = "FAILED_SAFE"
    EXHAUSTED = "EXHAUSTED"


@dataclass
class IncidentOutcome:
    incident_id: str
    final_state: AgentState
    attempts: int
    actions_tried: List[str]
    aht_seconds: float
    rto_seconds: Optional[float]
    rpo: Optional[RPOResult]
    diagnosis: Optional[Diagnosis]
    timeline: List[dict]

    def to_dict(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "final_state": self.final_state.value,
            "attempts": self.attempts,
            "actions_tried": self.actions_tried,
            "aht_seconds": self.aht_seconds,
            "rto_seconds": self.rto_seconds,
            "rpo": self.rpo.to_dict() if self.rpo else None,
            "diagnosis": (
                {
                    "root_cause": self.diagnosis.root_cause,
                    "evidence": self.diagnosis.evidence,
                    "confidence": self.diagnosis.confidence,
                    "recommended_action": self.diagnosis.recommended_action,
                }
                if self.diagnosis
                else None
            ),
            "timeline": self.timeline,
        }


class MasterAgent:
    def __init__(
        self,
        diagnosis_provider: DiagnosisProvider,
        knowledge_base: KnowledgeBase,
        action_executor: ActionExecutor,
        verifier: Verifier,
        escalation_policy: Optional[EscalationPolicy] = None,
        guardrail_engine: Optional[GuardrailEngine] = None,
        output_accounting: Optional[OutputAccounting] = None,
    ):
        self._diagnosis_provider = diagnosis_provider
        self._knowledge_base = knowledge_base
        self._action_executor = action_executor
        self._verifier = verifier
        self._escalation_policy = escalation_policy or EscalationPolicy()
        self._guardrails = guardrail_engine or GuardrailEngine()
        self._output_accounting = output_accounting
        self._lock = threading.Lock()

    def handle_incident(self, incident: Incident) -> IncidentOutcome:
        if self._lock.locked():
            logger.info(
                "MasterAgent busy -- queuing incident %s until the current one finishes",
                incident.incident_id,
            )
        with self._lock:
            return self._process(incident)

    # ------------------------------------------------------------
    # the state machine
    # ------------------------------------------------------------

    def _process(self, incident: Incident) -> IncidentOutcome:
        timeline = Timeline(incident.incident_id)
        aht_timer = AHTTimer()

        # ---- RECEIVED ----
        state = AgentState.RECEIVED
        aht_timer.start(incident.created_at)
        timeline.record(state.value, f"Incident {incident.incident_id} received ({incident.type.value})", aht_timer.elapsed())
        logger.info("[MasterAgent] %s: incident %s received", state.value, incident.incident_id)

        # ---- DIAGNOSING ----
        state = AgentState.DIAGNOSING
        history = self._knowledge_base.lookup(incident.type)
        diagnosis = self._diagnosis_provider.diagnose(incident, incident.telemetry_snapshot, history)
        timeline.record(
            state.value,
            f"Diagnosis: {diagnosis.root_cause} (confidence {diagnosis.confidence})",
            aht_timer.elapsed(),
            data={"root_cause": diagnosis.root_cause, "confidence": diagnosis.confidence, "evidence": diagnosis.evidence},
        )
        logger.info("[MasterAgent] %s: %s (confidence %.2f)", state.value, diagnosis.root_cause, diagnosis.confidence)

        # ---- CONSULTING_KNOWLEDGE ----
        state = AgentState.CONSULTING_KNOWLEDGE
        best_known_action = self._knowledge_base.most_successful_action(incident.type)
        timeline.record(
            state.value,
            f"Consulted history: {len(history)} past incident(s), best known action = {best_known_action}",
            aht_timer.elapsed(),
            data={"history_count": len(history), "most_successful_action": best_known_action},
        )
        logger.info("[MasterAgent] %s: %d historical record(s), best action=%s", state.value, len(history), best_known_action)

        # ---- decide / guardrail / act / verify loop ----
        already_attempted: List[str] = []
        rejected_by_guardrail: set = set()
        attempts_failed = 0
        final_state: Optional[AgentState] = None

        for _ in range(HARD_ITERATION_CAP):
            state = AgentState.DECIDING
            elapsed = aht_timer.elapsed()
            path = self._escalation_policy.decide(attempts_failed, elapsed)
            ESCALATION_PATH.labels(path=path.value).set(1)

            candidate = policy.next_action(incident.type, already_attempted, path)

            use_diagnosis = (
                diagnosis.recommended_action not in already_attempted
                and diagnosis.recommended_action not in rejected_by_guardrail
            )
            if use_diagnosis:
                chosen, source = diagnosis.recommended_action, "diagnosis"
            else:
                chosen, source = candidate, "policy"

            timeline.record(
                state.value,
                f"Chose action={chosen!r} via {source} on path={path.value}",
                elapsed,
                data={"chosen": chosen, "source": source, "path": path.value, "attempts_failed": attempts_failed},
            )
            logger.info("[MasterAgent] %s: chose %s (source=%s, path=%s)", state.value, chosen, source, path.value)

            exhausted = (
                chosen is None
                or chosen in already_attempted
                or attempts_failed >= policy.MAX_ATTEMPTS
                or len(already_attempted) >= policy.MAX_ATTEMPTS
            )
            if exhausted:
                final_state = self._run_failsafe(incident, timeline, aht_timer, already_attempted, diagnosis)
                break

            # ---- GUARDRAIL_CHECK ----
            state = AgentState.GUARDRAIL_CHECK
            decision = self._guardrails.check(chosen, incident, diagnosis)
            if not decision.allowed:
                rejected_by_guardrail.add(chosen)
                timeline.record(
                    state.value,
                    f"DENIED [{decision.rule}]: {decision.reason}",
                    aht_timer.elapsed(),
                    data={"denied_action": chosen, "rule": decision.rule},
                )
                logger.warning(
                    "[MasterAgent] %s: denied %r (%s: %s)", state.value, chosen, decision.rule, decision.reason
                )
                continue  # back to DECIDING for the next option

            timeline.record(
                state.value, f"Approved action={chosen!r} ({decision.rule})", aht_timer.elapsed(),
                data={"approved_action": chosen, "rule": decision.rule},
            )
            logger.info("[MasterAgent] %s: approved %s", state.value, chosen)

            # ---- ACTING ----
            state = AgentState.ACTING
            result = self._action_executor.execute(chosen, incident)
            already_attempted.append(chosen)
            AGENT_ACTIONS_TOTAL.labels(action=chosen, outcome="executed").inc()
            timeline.record(
                state.value,
                f"Executed {chosen!r}: {result.detail}",
                aht_timer.elapsed(),
                data={"action": chosen, "executed": result.executed, "detail": result.detail},
            )
            logger.info("[MasterAgent] %s: executed %s", state.value, chosen)

            # ---- VERIFYING ----
            state = AgentState.VERIFYING
            verification = self._verifier.verify()
            self._guardrails.release()  # action has finished, free the in-flight lock
            failed_note = "" if verification.passed else f" ({', '.join(verification.failed_checks)})"
            timeline.record(
                state.value,
                f"Verification {'passed' if verification.passed else 'failed'} after {chosen!r}{failed_note}",
                aht_timer.elapsed(),
                data={"passed": verification.passed, "failed_checks": verification.failed_checks},
            )
            logger.info(
                "[MasterAgent] %s: %s", state.value, "PASSED" if verification.passed else f"FAILED {verification.failed_checks}"
            )

            if verification.passed:
                AGENT_ACTIONS_TOTAL.labels(action=chosen, outcome="verified_ok").inc()
                final_state = AgentState.RESOLVED
                break

            AGENT_ACTIONS_TOTAL.labels(action=chosen, outcome="verified_failed").inc()
            attempts_failed += 1
            # loop back to DECIDING
        else:
            final_state = AgentState.EXHAUSTED
            timeline.record(final_state.value, "Hard iteration cap reached", aht_timer.elapsed())
            logger.error(
                "[MasterAgent] %s: hard iteration cap (%d) reached for incident %s",
                final_state.value, HARD_ITERATION_CAP, incident.incident_id,
            )

        # ---- terminal bookkeeping ----
        if final_state == AgentState.RESOLVED:
            incident.status = IncidentStatus.RESOLVED
            incident.resolved_at = datetime.now(timezone.utc)

        rto_seconds = compute_rto(incident)
        if rto_seconds is not None:
            RTO_SECONDS.labels(type=incident.type.value).observe(rto_seconds)
            timeline.record(
                final_state.value, f"Incident {incident.incident_id} resolved, RTO={rto_seconds:.2f}s", aht_timer.elapsed()
            )
            logger.info("[MasterAgent] %s: incident %s resolved (RTO=%.2fs)", final_state.value, incident.incident_id, rto_seconds)

        # RPO is computed regardless of final_state -- a FAILED_SAFE
        # incident's window still runs to now (see recovery_metrics.py).
        rpo = compute_rpo(incident, self._output_accounting)
        if rpo is not None:
            RPO_SECONDS.labels(type=incident.type.value).observe(rpo.seconds_affected)
            logger.info("[MasterAgent] RPO: %s", rpo.method)

        aht_timer.stop()
        AHT_SECONDS.labels(type=incident.type.value).observe(aht_timer.elapsed())

        return IncidentOutcome(
            incident_id=incident.incident_id,
            final_state=final_state,
            attempts=len(already_attempted),
            actions_tried=list(already_attempted),
            aht_seconds=aht_timer.elapsed(),
            rto_seconds=rto_seconds,
            rpo=rpo,
            diagnosis=diagnosis,
            timeline=timeline.to_list(),
        )

    def _run_failsafe(
        self, incident: Incident, timeline: Timeline, aht_timer: AHTTimer, already_attempted: List[str], diagnosis: Diagnosis
    ) -> AgentState:
        """Terminal branch: execute FAILSAFE_ACTION once, verify once,
        then stop -- never loops. If failover was already tried as part
        of the normal ladder (e.g. via the FAILSAFE escalation path), it
        is not re-executed, so "exactly once" holds regardless of which
        path led here. Still goes through GUARDRAIL_CHECK -- "never
        execute an unvalidated action" applies here too."""
        state = AgentState.FAILED_SAFE
        action = policy.FAILSAFE_ACTION

        if action in already_attempted:
            timeline.record(
                state.value,
                f"Failsafe action {action!r} was already attempted and failed -- stopping without re-executing",
                aht_timer.elapsed(),
            )
            logger.warning(
                "[MasterAgent] %s: %s already attempted for incident %s; stopping", state.value, action, incident.incident_id
            )
            return AgentState.FAILED_SAFE

        decision = self._guardrails.check(action, incident, diagnosis)
        if not decision.allowed:
            timeline.record(
                AgentState.GUARDRAIL_CHECK.value,
                f"DENIED [{decision.rule}]: {decision.reason}",
                aht_timer.elapsed(),
                data={"denied_action": action, "rule": decision.rule},
            )
            logger.warning("[MasterAgent] %s: failsafe action %r denied (%s: %s)", state.value, action, decision.rule, decision.reason)
            return AgentState.FAILED_SAFE

        timeline.record(state.value, f"Exhausted normal options -- executing failsafe action {action!r}", aht_timer.elapsed())
        logger.warning("[MasterAgent] %s: executing failsafe action %s for incident %s", state.value, action, incident.incident_id)

        result = self._action_executor.execute(action, incident)
        already_attempted.append(action)
        AGENT_ACTIONS_TOTAL.labels(action=action, outcome="executed").inc()
        timeline.record(state.value, f"Executed failsafe action {action!r}: {result.detail}", aht_timer.elapsed())

        verification = self._verifier.verify()
        self._guardrails.release()
        timeline.record(
            state.value,
            f"Failsafe verification {'passed' if verification.passed else 'failed'}",
            aht_timer.elapsed(),
            data={"passed": verification.passed, "failed_checks": verification.failed_checks},
        )
        outcome = "verified_ok" if verification.passed else "verified_failed"
        AGENT_ACTIONS_TOTAL.labels(action=action, outcome=outcome).inc()

        return AgentState.RESOLVED if verification.passed else AgentState.FAILED_SAFE
