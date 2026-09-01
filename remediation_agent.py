"""
The Remediation Agent -- real Gemini (via Vertex AI, using Google ADK).

Given the validated `IncidentEvidence` (from the Aggregator) plus the retrieved
KB precedent (`list[KBMatch]` from the Knowledge Base + RAG step), Gemini reasons
over BOTH and proposes EXACTLY ONE action from the fixed enum:

    RESTART_ENCODER | REDUCE_PROFILE | SWITCH_SOURCE | FAILOVER

The proposal is ADVICE ONLY. This agent has no execution authority:
  - it never calls simulator/control.py,
  - it never touches infrastructure,
  - it never maps the enum to a control method.
The proposal goes to the deterministic Safety Gate next, then the Control Plane.

Enum guardrail (Tier 1): `_RemediationResponse.action` is typed `RemediationAction`,
so any value that is not one of the 4 members fails Pydantic validation. Gemini
cannot invent an action. On an invalid response we do ONE strict retry; if it
still fails, `propose_remediation()` returns None -- never a malformed proposal.

Contract of `propose_remediation()`:
  - returns a valid RemediationProposal on success;
  - returns None if Gemini's output fails validation twice
    -> the caller must STOP (no proposal);
  - raises on infrastructure failure (Gemini API/auth/quota error, empty
    response) -- also fail-closed at the caller.

Standalone: `python3 remediation_agent.py [overload|failure|rgb|network]`
Prereqs: KB seeded (python3 seed_knowledge_base.py), ADC configured, Vertex AI enabled.
"""

from __future__ import annotations

import logging
import os
import sys
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.runners import InMemoryRunner
from google.genai import types

from models import (
    IncidentEvidence,
    KBMatch,
    RemediationAction,
    RemediationProposal,
)

logger = logging.getLogger("remediation_agent")

# --------------------------------------------------------------------------- #
# Config -- module constants, os.environ with defaults (matches the other agents)
# --------------------------------------------------------------------------- #

try:  # pragma: no cover - convenience only
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

GCP_PROJECT_ID = os.environ.get("GCP_PROJECT_ID", "broadcast-ops-copilot")
VERTEX_LOCATION = (
    os.environ.get("GOOGLE_CLOUD_LOCATION")
    or os.environ.get("GCP_REGION")
    or "us-central1"
)
REMEDIATION_MODEL = os.environ.get("REMEDIATION_MODEL", "gemini-2.5-flash")

MAX_ATTEMPTS = 2  # one call + one strict retry
_APP_NAME = "mediaops_remediation_agent"


# --------------------------------------------------------------------------- #
# 1. The strict output contract -- this is the enum guardrail
# --------------------------------------------------------------------------- #

class _RemediationResponse(BaseModel):
    """The exact JSON contract asked of Gemini. Validated strictly.

    `action: RemediationAction` is the structural guardrail: a value outside the
    4-member enum fails validation here and can never reach a RemediationProposal.
    """

    model_config = ConfigDict(extra="forbid")

    action: RemediationAction
    rationale: str = Field(min_length=1, max_length=1000)
    confidence: float = Field(ge=0.0, le=1.0)


_INSTRUCTION = """You are the read-only MediaOps Remediation Agent for a live video stream.

You are ADVISORY ONLY. A separate deterministic Safety Gate decides whether your
proposal is allowed, and a separate Control Plane executes it. You never execute
anything and never touch infrastructure. Your job is to propose EXACTLY ONE
remediation action, chosen from this fixed list -- you may not invent others:

- RESTART_ENCODER: restarts the encoder process. Clears encoder-layer fault
  (queue depth, resource load, dropped-frame accumulator). Does NOT fix a
  degraded network and does NOT change bitrate. Lowest blast radius. Usual first
  choice for encoder overload or a crashed/failed encoder.
- REDUCE_PROFILE: lowers the encoder bitrate to 0.5x nominal. Relieves SUSTAINED
  overload that a bare restart would just hit again. Keeps the primary path but
  causes a visible quality drop.
- SWITCH_SOURCE: cuts the active output to the standby backup encoder. Use when
  the primary encoder is unrecoverable, or a restart has already been tried and
  failed. Medium blast radius.
- FAILOVER: blunt last resort -- switch to backup AND drop bitrate to 0.3x AND
  restart. Only for severe cases where nothing safer will do.

Use BOTH the current incident evidence AND the retrieved precedent:
- If similar past incidents were resolved by a specific action, that is strong
  support for that action -- unless the current evidence differs materially.
- If a precedent shows outcome = not_resolved, do NOT repeat that action for a
  similar incident.
- If there is no precedent, decide from the evidence alone and lower your
  confidence accordingly.

Confidence: high only when the evidence is strong AND the precedent agrees. Lower
it when precedent is absent, thin, or conflicting, or when vision and infra
disagree (agreement = False).

Return ONLY a JSON object with exactly these keys and nothing else:
  "action": one of RESTART_ENCODER, REDUCE_PROFILE, SWITCH_SOURCE, FAILOVER
  "rationale": a short string that explicitly references the evidence AND the precedent
  "confidence": a number between 0 and 1
No markdown, no commentary.
"""

_RETRY_SUFFIX = """

YOUR PREVIOUS RESPONSE FAILED VALIDATION: {error}
Return ONLY a JSON object with exactly these three keys and nothing else:
  "action": one of RESTART_ENCODER, REDUCE_PROFILE, SWITCH_SOURCE, FAILOVER
  "rationale": a short string
  "confidence": a number between 0 and 1
"""


# --------------------------------------------------------------------------- #
# 2. Render the precedent -- reused for the prompt AND for precedent_summary
# --------------------------------------------------------------------------- #

def _render_precedent(precedent: list[KBMatch]) -> str:
    if not precedent:
        return (
            "RETRIEVED PRECEDENT: none cleared the relevance threshold -- "
            "decide from the current evidence alone."
        )
    lines = [f"RETRIEVED PRECEDENT  ({len(precedent)} past incidents above the RAG relevance threshold)"]
    for m in precedent:
        lines.append(
            f"  - {m.kb_id}  similarity {m.similarity}  |  {m.fault_class}  |  "
            f"action_taken {m.action_taken.value} -> {m.outcome}"
        )
        lines.append(f"    {m.summary}")
    return "\n".join(lines)


def _build_context(evidence: IncidentEvidence, precedent: list[KBMatch]) -> str:
    v = evidence.vision
    inf = evidence.infra
    metrics = ", ".join(f"{k} {val}" for k, val in list(inf.supporting_metrics.items())[:6])
    return (
        "INCIDENT EVIDENCE\n"
        f"  fault_class: {evidence.fault_class.value}\n"
        f"  vision vs infra agreement: {evidence.agreement}\n"
        f"  overall evidence confidence: {evidence.confidence}\n"
        f"  summary: {evidence.summary}\n"
        f"  vision: {v.symptom.value} (conf {v.confidence}) -- {v.description}\n"
        f"  infra:  {inf.fault_class.value} on {inf.affected_component} (conf {inf.confidence}); "
        f"metrics: {metrics}\n\n"
        f"{_render_precedent(precedent)}"
    )


# --------------------------------------------------------------------------- #
# 3. The ADK agent + the real Gemini call
# --------------------------------------------------------------------------- #

def _build_agent(instruction: str) -> LlmAgent:
    return LlmAgent(
        name=_APP_NAME,
        model=Gemini(
            model=REMEDIATION_MODEL,
            client_kwargs={
                "vertexai": True,
                "project": GCP_PROJECT_ID,
                "location": VERTEX_LOCATION,
            },
        ),
        instruction=instruction,
        output_schema=_RemediationResponse,
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def _call_gemini(context: str, instruction: str) -> str:
    """
    One real ADK runtime call to Gemini on Vertex AI. Returns the raw final text
    (JSON). This is the single seam a test monkey-patches.
    """
    agent = _build_agent(instruction)
    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)
    runner.session_service.create_session_sync(
        app_name=_APP_NAME, user_id="remediation", session_id="remediation"
    )

    message = types.Content(role="user", parts=[types.Part.from_text(text=context)])

    final: Optional[str] = None
    for event in runner.run(
        user_id="remediation", session_id="remediation", new_message=message
    ):
        if event.is_final_response() and event.content and event.content.parts:
            final = event.content.parts[0].text
    if not final:
        raise RuntimeError("Gemini returned no final response")
    return final


# --------------------------------------------------------------------------- #
# 4. propose_remediation -- orchestration + the strict guardrail
# --------------------------------------------------------------------------- #

def propose_remediation(
    evidence: IncidentEvidence,
    precedent: list[KBMatch],
) -> Optional[RemediationProposal]:
    """See module docstring for the contract. Proposes ONE action; never executes."""
    ctx = f" (incident {evidence.incident_id})"
    context = _build_context(evidence, precedent)
    logger.info(
        "remediation%s: fault=%s  agreement=%s  precedent=%d",
        ctx, evidence.fault_class.value, evidence.agreement, len(precedent),
    )

    instruction = _INSTRUCTION
    for attempt in range(1, MAX_ATTEMPTS + 1):
        raw = _call_gemini(context, instruction)  # infra failure propagates
        try:
            parsed = _RemediationResponse.model_validate_json(raw)
        except (ValidationError, ValueError) as exc:
            logger.warning("remediation%s attempt %d: invalid response: %s", ctx, attempt, exc)
            instruction = _INSTRUCTION + _RETRY_SUFFIX.format(error=str(exc)[:300])
            continue

        logger.info(
            "remediation%s attempt %d: %s (confidence %.2f)",
            ctx, attempt, parsed.action.value, parsed.confidence,
        )
        return RemediationProposal(
            incident_id=evidence.incident_id,
            action=parsed.action,
            rationale=parsed.rationale,
            confidence=parsed.confidence,
            model=REMEDIATION_MODEL,
            similar_incident_ids=[m.kb_id for m in precedent],
            precedent_summary=_render_precedent(precedent) if precedent else None,
            raw_response=raw,
        )

    logger.error(
        "remediation%s: %d invalid responses -- returning None (caller should stop)",
        ctx, MAX_ATTEMPTS,
    )
    return None


# --------------------------------------------------------------------------- #
# Manual run
# --------------------------------------------------------------------------- #

_DEMO_SUMMARIES = {
    "overload": (
        "encoder_overload on encoder_01. Infra (conf 0.95): media_cpu_usage_percent 97.0, "
        "media_fps 18.0, media_encoding_latency_ms 190.0. Vision (conf 0.95): MACROBLOCKING "
        "-- severe blocky compression artifacts across the entire frame. Video symptom "
        "corroborates the telemetry fault class. Overall evidence confidence 0.95."
    ),
    "failure": (
        "encoder_failure on encoder_01. Infra (conf 0.95): media_encoder_status 0.0, "
        "media_fps 0.0, media_bitrate_mbps 0.0, media_dropped_frames_percent 100.0. Vision "
        "(conf 1.0): BLACK_FRAME -- the screen is entirely black. Video symptom corroborates "
        "the telemetry fault class. Overall evidence confidence 0.95."
    ),
    "rgb": (
        "unknown on encoder_01. Infra (conf 0.3): telemetry nominal. Vision (conf 0.95): "
        "RGB_SHIFT -- persistent red and cyan colour fringing and misregistration on every "
        "edge. Video symptom RGB_SHIFT carries no telemetry signature. Overall evidence "
        "confidence 0.29."
    ),
    "network": (
        "network_degradation on network path. Infra (conf 0.9): media_packet_loss_percent "
        "14.0, media_network_latency_ms 480.0, media_fps 16.0. Vision (conf 0.8): NORMAL -- "
        "picture looks fine. Video symptom NORMAL carries no telemetry signature. Overall "
        "evidence confidence 0.4."
    ),
}

_DEMO_FAULT = {
    "overload": ("encoder_overload", "MACROBLOCKING", True),
    "failure": ("encoder_failure", "BLACK_FRAME", True),
    "rgb": ("unknown", "RGB_SHIFT", False),
    "network": ("network_degradation", "NORMAL", False),
}


def _demo_evidence(kind: str) -> IncidentEvidence:
    from models import FaultClass, InfraFinding, VisionFinding, VisionSymptom

    now = datetime.now(timezone.utc)
    fault_value, symptom_name, agreement = _DEMO_FAULT[kind]
    return IncidentEvidence(
        incident_id=f"demo-{kind}",
        vision=VisionFinding(
            frame_captured_at=now, observed_at=now,
            symptom=VisionSymptom[symptom_name], description="demo frame",
            confidence=0.9, model=REMEDIATION_MODEL, raw_response="{}",
        ),
        infra=InfraFinding(
            observed_at=now, fault_class=FaultClass(fault_value),
            affected_component="encoder_01", description="demo telemetry",
            supporting_metrics={"media_fps": 18.0, "media_cpu_usage_percent": 97.0},
            confidence=0.9, model=REMEDIATION_MODEL, raw_response="{}",
        ),
        agreement=agreement, fault_class=FaultClass(fault_value), confidence=0.9,
        summary=_DEMO_SUMMARIES[kind], validation_passed=True,
    )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    kind = sys.argv[1] if len(sys.argv) > 1 else "overload"
    if kind not in _DEMO_SUMMARIES:
        print(f"usage: python3 remediation_agent.py [{'|'.join(_DEMO_SUMMARIES)}]")
        sys.exit(2)

    import knowledge_base

    evidence = _demo_evidence(kind)
    try:
        precedent = knowledge_base.retrieve(evidence)
    except RuntimeError as exc:
        print(f"KB not available: {exc}")
        precedent = []

    print("\n" + _render_precedent(precedent) + "\n")

    proposal = propose_remediation(evidence, precedent)
    if proposal is None:
        print("\nNone -- no proposal (Gemini output failed validation twice)")
    else:
        print()
        print(proposal.model_dump_json(indent=2))
