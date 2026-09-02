"""
Pydantic data contracts for the MediaOps CoPilot incident-response workflow.

These are the shapes every component hands to the next one:

    Detector           -> AnomalyEvent
    Incident Recorder  -> Incident            (authoritative Firestore state)
    Vision Agent        -> VisionFinding
    Infra Agent         -> InfraFinding
    Evidence Aggregator -> IncidentEvidence
    Remediation Agent   -> RemediationProposal (suggestion only)
    Safety Gate         -> SafetyDecision      (deterministic, no AI, fail-closed)
    Control Plane        -> executes control.py, records onto Incident
    Verify Recovery      -> VerificationResult  (deterministic, needs real evidence)

No behaviour lives here -- just field shapes and validation. This module has no
dependency on the simulator and never imports it. The enum -> control.py method
mapping belongs to the Control Plane, not here.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# --------------------------------------------------------------------------- #
# Small shared helpers
# --------------------------------------------------------------------------- #

def _utcnow() -> datetime:
    """Timezone-aware UTC now. Matches the convention used across the codebase."""
    return datetime.now(timezone.utc)


def _new_id() -> str:
    """Short random identifier for events and incidents."""
    return uuid.uuid4().hex


class StrictModel(BaseModel):
    """
    Base for every contract in this file.

    - ``extra="forbid"``: an unexpected field is a validation error, not silently
      dropped. Tier 1 requires strict schema validation, especially for the
      Vision finding coming back from Gemini.
    - ``validate_assignment=True``: mutating a field after construction re-runs
      validation, so the orchestrator can't push an object into a bad state.
    """

    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --------------------------------------------------------------------------- #
# Shared enums
# --------------------------------------------------------------------------- #

class Severity(str, Enum):
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class FaultClass(str, Enum):
    """
    What kind of fault we think is happening. String values match the simulator's
    own ``failure_mode`` literals (see simulator/failures.py) so they line up
    across the process boundary. ``UNKNOWN`` is for when the Infra Agent can't
    classify confidently.
    """

    ENCODER_OVERLOAD = "encoder_overload"
    NETWORK_DEGRADATION = "network_degradation"
    ENCODER_FAILURE = "encoder_failure"
    UNKNOWN = "unknown"


class IncidentStatus(str, Enum):
    """Where the incident is in the workflow. Only deterministic code advances this."""

    DETECTED = "DETECTED"
    DIAGNOSING = "DIAGNOSING"
    AGGREGATING = "AGGREGATING"
    RETRIEVING = "RETRIEVING"
    DECIDING = "DECIDING"
    GATING = "GATING"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    BLOCKED = "BLOCKED"
    CANNOT_VERIFY = "CANNOT_VERIFY"
    AUTOMATION_FAILED = "AUTOMATION_FAILED"
    RECOVERED = "RECOVERED"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"
    ESCALATED = "ESCALATED"
    CLOSED = "CLOSED"


# Statuses that mean the incident is over. Used by the Incident Recorder to
# decide whether an existing incident is still "active" (one active incident
# per problem). Authoritative domain knowledge -- lives with the enum.
TERMINAL_STATUSES = frozenset({
    IncidentStatus.RECOVERED,
    IncidentStatus.BLOCKED,
    IncidentStatus.CANNOT_VERIFY,
    IncidentStatus.AUTOMATION_FAILED,
    IncidentStatus.RESOLVED,
    IncidentStatus.FAILED,
    IncidentStatus.ESCALATED,
    IncidentStatus.CLOSED,
})


class RemediationAction(str, Enum):
    """
    The FIXED set of actions the Remediation Agent may choose from. It cannot
    invent commands -- this enum enforces that structurally. Must stay in sync
    with the Control Plane's mapping to simulator/control.py methods:

        RESTART_ENCODER -> restart_encoder()
        REDUCE_PROFILE  -> reduce_bitrate(0.5)
        SWITCH_SOURCE   -> switch_backup()
        FAILOVER        -> failover()
    """

    RESTART_ENCODER = "RESTART_ENCODER"
    REDUCE_PROFILE = "REDUCE_PROFILE"
    SWITCH_SOURCE = "SWITCH_SOURCE"
    FAILOVER = "FAILOVER"


class VisionSymptom(str, Enum):
    """Closed vocabulary for what the Vision Agent sees in a frame -- no free text."""

    NORMAL = "NORMAL"
    BLACK_FRAME = "BLACK_FRAME"
    FROZEN_FRAME = "FROZEN_FRAME"
    MACROBLOCKING = "MACROBLOCKING"
    RGB_SHIFT = "RGB_SHIFT"
    COLOR_BARS = "COLOR_BARS"
    SLATE = "SLATE"
    OTHER = "OTHER"


class SafetyVerdict(str, Enum):
    ALLOW = "ALLOW"
    BLOCK = "BLOCK"


class VerificationVerdict(str, Enum):
    RECOVERED = "RECOVERED"
    RECOVERY_FAILED = "RECOVERY_FAILED"
    CANNOT_VERIFY = "CANNOT_VERIFY"


class LifecycleEvent(StrictModel):
    """One durable, ordered breadcrumb in an incident's lifecycle."""

    timestamp: datetime = Field(default_factory=_utcnow)
    component: str = Field(min_length=1)
    step: str = Field(min_length=1)
    outcome: str = Field(min_length=1)
    status: Optional[IncidentStatus] = None
    action: Optional[RemediationAction] = None
    detail: str = ""


# --------------------------------------------------------------------------- #
# 1. AnomalyEvent -- output of the Detector
# --------------------------------------------------------------------------- #

class AnomalyEvent(StrictModel):
    """
    One confirmed anomaly. The Detector emits exactly one of these after its
    threshold + persistence + dedup logic fires.
    """

    event_id: str = Field(default_factory=_new_id)
    detected_at: datetime = Field(default_factory=_utcnow)
    fault_class: FaultClass
    severity: Severity
    reason: str = Field(min_length=1, description="Human summary, e.g. 'fps 18 < 24 for 3 samples'")
    health_value: int = Field(ge=0, le=1, description="media_pipeline_health at detection (0 = broken)")
    telemetry_snapshot: dict[str, float] = Field(default_factory=dict)
    breach_count: int = Field(ge=1, description="Consecutive breaching samples that confirmed the anomaly")
    source: Literal["prometheus"] = "prometheus"


# --------------------------------------------------------------------------- #
# 3. VisionFinding -- Gemini reads one recent frame (strict, Tier 1)
# --------------------------------------------------------------------------- #

class VisionFinding(StrictModel):
    """
    Structured result of the Vision Agent looking at a single recent frame.
    Strictly validated: a malformed model response must fail here, not leak
    downstream.
    """

    source: Literal["vision"] = "vision"
    observed_at: datetime = Field(default_factory=_utcnow)
    frame_captured_at: datetime = Field(description="Timestamp of the frame the agent looked at")
    symptom: VisionSymptom
    description: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)
    model: str = Field(min_length=1, description="Model id, e.g. 'gemini-2.0-flash'")
    raw_response: Optional[str] = Field(default=None, description="Verbatim model output, kept for audit")


# --------------------------------------------------------------------------- #
# 4. InfraFinding -- Gemini + read-only bounded Prometheus/Grafana
# --------------------------------------------------------------------------- #

class InfraFinding(StrictModel):
    """
    Structured result of the Infra Agent reasoning over bounded, read-only
    telemetry. It classifies the fault; it never executes or approves anything.
    """

    source: Literal["infra"] = "infra"
    observed_at: datetime = Field(default_factory=_utcnow)
    fault_class: FaultClass
    affected_component: str = Field(min_length=1, description="e.g. 'encoder_01'")
    description: str = Field(min_length=1, max_length=500)
    supporting_metrics: dict[str, float] = Field(default_factory=dict, description="The bounded queries it read")
    confidence: float = Field(ge=0.0, le=1.0)
    model: str = Field(min_length=1)
    raw_response: Optional[str] = None


# --------------------------------------------------------------------------- #
# 5. IncidentEvidence -- Evidence Aggregator output
# --------------------------------------------------------------------------- #

class IncidentEvidence(StrictModel):
    """
    Both findings, validated (schema / source / time) and combined. ``summary``
    is the text the RAG step embeds to retrieve similar past incidents.
    """

    incident_id: str
    aggregated_at: datetime = Field(default_factory=_utcnow)
    vision: VisionFinding
    infra: InfraFinding
    agreement: bool = Field(description="Do the video symptom and the fault class corroborate each other")
    fault_class: FaultClass = Field(
        description="Agreed fault class (infra's, corroborated or abstained-on by vision)"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Overall evidence confidence")
    summary: str = Field(min_length=1, description="Combined text used as the RAG embedding input")
    validation_passed: bool
    validation_errors: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# 5b. KBMatch -- one retrieved past incident (Knowledge Base + RAG)
# --------------------------------------------------------------------------- #

class KBMatch(StrictModel):
    """
    One past incident retrieved from the Knowledge Base -- precedent context for
    the Remediation Agent. NOT a recommendation: ``action_taken`` is what was done
    historically, ``outcome`` whether it worked. The agent decides; this informs.
    """

    kb_id: str
    fault_class: str = Field(description="fault of the past incident (KB taxonomy, broader than FaultClass)")
    action_taken: RemediationAction
    outcome: str = Field(description="'resolved' or 'not_resolved'")
    similarity: float = Field(ge=-1.0, le=1.0, description="cosine similarity to the query evidence")
    summary: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 6. RemediationProposal -- Remediation Agent picks ONE action (suggestion only)
# --------------------------------------------------------------------------- #

class RemediationProposal(StrictModel):
    """
    The Remediation Agent's single suggested action, drawn only from
    ``RemediationAction``. This is advice -- it grants no authority to execute.
    """

    incident_id: str
    proposed_at: datetime = Field(default_factory=_utcnow)
    action: RemediationAction
    rationale: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)
    model: str = Field(min_length=1)
    similar_incident_ids: list[str] = Field(default_factory=list, description="RAG hits above the relevance threshold")
    precedent_summary: Optional[str] = Field(default=None, description="What past cases suggested")
    raw_response: Optional[str] = None


# --------------------------------------------------------------------------- #
# 7. SafetyDecision -- deterministic gate, NO AI, fail-closed
# --------------------------------------------------------------------------- #

class SafetyCheck(StrictModel):
    """One line item in the Safety Gate's evaluation."""

    name: str = Field(min_length=1, description="e.g. 'allow_list', 'compatibility', 'confidence', 'budget', 'cooldown'")
    passed: bool
    detail: str = Field(default="")


class SafetyDecision(StrictModel):
    """
    The deterministic gate's verdict. ``verdict`` is ``ALLOW`` only when every
    check passed; any failed check or error -> ``BLOCK`` with a ``block_reason``.
    """

    incident_id: str
    evaluated_at: datetime = Field(default_factory=_utcnow)
    action: RemediationAction
    verdict: SafetyVerdict
    checks: list[SafetyCheck] = Field(default_factory=list)
    block_reason: Optional[str] = Field(default=None, description="Set iff verdict == BLOCK; the first failed check or error")
    idempotency_key: str = Field(min_length=1, description="Handed to the Control Plane so a duplicate can't execute twice")


# --------------------------------------------------------------------------- #
# 8. ExecutionResult -- Control Plane command result (NOT recovery)
# --------------------------------------------------------------------------- #

class ExecutionResult(StrictModel):
    """What the deterministic Control Plane ran and whether that call succeeded.

    ``success`` describes only the control call. It never means the incident
    recovered; independent verification owns that decision.
    """

    incident_id: str
    executed_at: datetime = Field(default_factory=_utcnow)
    action: RemediationAction
    idempotency_key: str = Field(min_length=1)
    success: bool
    detail: str = Field(min_length=1)


# --------------------------------------------------------------------------- #
# 9. VerificationResult -- deterministic recovery check (BOTH video + telemetry)
# --------------------------------------------------------------------------- #

class VerificationResult(StrictModel):
    """
    The independent recovery check. ``recovered`` is true only when BOTH the
    telemetry held healthy across the stable window AND a fresh frame looks
    normal. "The control API returned success" is not recovery.
    """

    incident_id: str
    verified_at: datetime = Field(default_factory=_utcnow)
    action: RemediationAction = Field(description="The action that was actually executed")
    verdict: VerificationVerdict
    recovered: bool
    telemetry_ok: bool
    video_ok: bool
    health_value: Optional[int] = Field(
        default=None,
        ge=0,
        le=1,
        description="media_pipeline_health at window end, or None when unavailable",
    )
    stable_window_seconds: float = Field(ge=0.0, description="How long health was observed to hold")
    samples: list[dict[str, float]] = Field(default_factory=list, description="Readings taken across the window")
    failed_checks: list[str] = Field(default_factory=list)
    vision_recheck: Optional[VisionFinding] = Field(default=None, description="The confirming re-look at the video")


# --------------------------------------------------------------------------- #
# 2. Incident -- durable Firestore record, authoritative workflow state
# --------------------------------------------------------------------------- #

class Incident(StrictModel):
    """
    The one authoritative document per problem. Sub-findings are nested and
    optional; the orchestrator fills them in as the workflow advances and
    persists the whole object back to Firestore after each step.
    """

    incident_id: str = Field(default_factory=_new_id)
    status: IncidentStatus = IncidentStatus.DETECTED
    anomaly: AnomalyEvent
    repeated_anomalies: list[AnomalyEvent] = Field(
        default_factory=list,
        description="Further AnomalyEvents seen for this same still-active incident (dedup path)",
    )

    created_at: datetime = Field(default_factory=_utcnow)
    updated_at: datetime = Field(default_factory=_utcnow)
    closed_at: Optional[datetime] = None
    current_step: str = Field(default="detect", description="Name of the orchestrator step in progress")
    lifecycle_events: list[LifecycleEvent] = Field(
        default_factory=list,
        description="Append-only structured timeline for tracing and demos",
    )
    terminal_step: Optional[str] = None
    terminal_reason: Optional[str] = None

    vision: Optional[VisionFinding] = Field(default=None, description="Vision Agent output, before aggregation")
    infra: Optional[InfraFinding] = Field(default=None, description="Infra Agent output, before aggregation")
    evidence: Optional[IncidentEvidence] = None
    precedent: list[KBMatch] = Field(default_factory=list, description="RAG hits from the Knowledge Base")
    proposal: Optional[RemediationProposal] = None
    safety_decision: Optional[SafetyDecision] = None
    proposal_history: list[RemediationProposal] = Field(default_factory=list)
    safety_decision_history: list[SafetyDecision] = Field(default_factory=list)
    execution: Optional[ExecutionResult] = Field(
        default=None,
        description="Control call result only; does not establish recovery",
    )
    verification: Optional[VerificationResult] = None

    execution_history: list[ExecutionResult] = Field(
        default_factory=list,
        description="Prior control-call results retained when a fallback replaces the current attempt",
    )
    verification_history: list[VerificationResult] = Field(
        default_factory=list,
        description="Prior verification results retained when a fallback is attempted",
    )
    fallback_attempted: bool = False
    automation_failure_reason: Optional[str] = None

    actions_attempted: list[RemediationAction] = Field(default_factory=list)
    attempt_count: int = Field(default=0, ge=0)
    idempotency_key: Optional[str] = Field(default=None, description="Set once, before the first execution")
    report_sent: bool = False
    report_claimed: bool = False
    report_error: Optional[str] = None
    kb_writeback_id: Optional[str] = None
    kb_writeback_error: Optional[str] = None
    notes: list[str] = Field(default_factory=list, description="Append-only human-readable log lines")
