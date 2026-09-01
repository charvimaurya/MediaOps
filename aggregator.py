"""
The Evidence Aggregator -- a deterministic validating gate. No AI, no I/O.

Takes the VisionFinding + InfraFinding for an incident, runs four ordered checks,
and either produces a validated IncidentEvidence (from models.py) or stops the
workflow with a clear reason:

    1. both present   -> IncompleteEvidence if either is None
    2. valid          -> IncompleteEvidence on a bad source / timestamp / missing
                         raw_response / empty supporting_metrics / low confidence
    3. agreement      -> EvidenceConflict if the video symptom and the telemetry
                         fault class point at genuinely different faults
    4. package        -> one IncidentEvidence with the agreed fault class + an
                         overall confidence + a template summary (RAG input)

Disagreement fails CLOSED (EvidenceConflict), not "low confidence" -- Vision and
Infra are independent diagnostic paths, and a real contradiction means the
incident is ambiguous or an agent hallucinated. Neither is safe to auto-remediate.

Standalone: `aggregate(incident_id, vision, infra)`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from models import FaultClass, IncidentEvidence, InfraFinding, VisionFinding, VisionSymptom

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #

EVIDENCE_MAX_AGE_SECONDS = 3600      # a finding older than this is stale
CLOCK_SKEW_SECONDS = 60              # tolerance for a timestamp "in the future"
MIN_FINDING_CONFIDENCE = 0.0         # a finding below this is not real evidence (knob)
ABSTAIN_CONFIDENCE_FACTOR = 0.5      # overall-confidence multiplier when a side abstained

# Which fault classes each viewer-visible symptom is *consistent with*. A set,
# not a 1:1 map -- MACROBLOCKING genuinely fits both overload and packet loss, so
# a strict map would raise false conflicts. An empty set = "no telemetry
# correlate" = vision abstains from the agreement check.
COMPAT: dict[VisionSymptom, frozenset[FaultClass]] = {
    VisionSymptom.MACROBLOCKING: frozenset(
        {FaultClass.ENCODER_OVERLOAD, FaultClass.NETWORK_DEGRADATION}
    ),
    VisionSymptom.BLACK_FRAME: frozenset({FaultClass.ENCODER_FAILURE}),
    VisionSymptom.FROZEN_FRAME: frozenset(
        {FaultClass.ENCODER_FAILURE, FaultClass.NETWORK_DEGRADATION}
    ),
    VisionSymptom.RGB_SHIFT: frozenset(),
    VisionSymptom.NORMAL: frozenset(),
    VisionSymptom.COLOR_BARS: frozenset(),
    VisionSymptom.SLATE: frozenset(),
    VisionSymptom.OTHER: frozenset(),
}


# --------------------------------------------------------------------------- #
# Stop signals -- carry the reason; the orchestrator fail-closes on exceptions
# --------------------------------------------------------------------------- #

class AggregationError(Exception):
    """Base: the evidence gate stopped the workflow."""


class IncompleteEvidence(AggregationError):
    """A finding is missing or fails the validity checks."""


class EvidenceConflict(AggregationError):
    """Vision and Infra point at genuinely different faults."""


# --------------------------------------------------------------------------- #
# The gate
# --------------------------------------------------------------------------- #

def aggregate(
    incident_id: str,
    vision: Optional[VisionFinding],
    infra: Optional[InfraFinding],
) -> IncidentEvidence:
    """See module docstring. Returns a validated IncidentEvidence or raises."""

    # -- check 1: both present ------------------------------------------- #
    if vision is None:
        raise IncompleteEvidence("vision finding missing -- cannot build evidence")
    if infra is None:
        raise IncompleteEvidence("infra finding missing -- cannot build evidence")

    # -- check 2: valid ----------------------------------------------- #
    _check_valid(vision, infra)

    # -- check 3: agreement --------------------------------------- #
    compatible = COMPAT[vision.symptom]
    vision_abstains = not compatible
    infra_abstains = infra.fault_class is FaultClass.UNKNOWN
    agreement = infra.fault_class in compatible

    if not agreement and not vision_abstains and not infra_abstains:
        raise EvidenceConflict(
            f"agreement: vision symptom {vision.symptom.value} is consistent with "
            f"{sorted(f.value for f in compatible)}, but infra classified "
            f"{infra.fault_class.value}"
        )

    # -- check 4: package ------------------------------------ #
    agreed = infra.fault_class
    base = min(vision.confidence, infra.confidence)
    overall = base if agreement else round(base * ABSTAIN_CONFIDENCE_FACTOR, 3)

    return IncidentEvidence(
        incident_id=incident_id,
        vision=vision,
        infra=infra,
        agreement=agreement,
        fault_class=agreed,
        confidence=overall,
        summary=_build_summary(vision, infra, agreed, agreement, overall),
        validation_passed=True,
        validation_errors=[],
    )


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def _check_valid(vision: VisionFinding, infra: InfraFinding) -> None:
    if vision.source != "vision":
        raise IncompleteEvidence(f"valid: vision.source is {vision.source!r}, expected 'vision'")
    if infra.source != "infra":
        raise IncompleteEvidence(f"valid: infra.source is {infra.source!r}, expected 'infra'")

    if vision.raw_response is None:
        raise IncompleteEvidence("valid: vision.raw_response missing (not a real model call)")
    if infra.raw_response is None:
        raise IncompleteEvidence("valid: infra.raw_response missing (not a real model call)")

    if not infra.supporting_metrics:
        raise IncompleteEvidence("valid: infra.supporting_metrics empty (no telemetry was read)")

    if vision.confidence < MIN_FINDING_CONFIDENCE:
        raise IncompleteEvidence(f"valid: vision.confidence {vision.confidence} below floor")
    if infra.confidence < MIN_FINDING_CONFIDENCE:
        raise IncompleteEvidence(f"valid: infra.confidence {infra.confidence} below floor")

    now = datetime.now(timezone.utc)
    future = now + timedelta(seconds=CLOCK_SKEW_SECONDS)
    stale = now - timedelta(seconds=EVIDENCE_MAX_AGE_SECONDS)
    stamps = {
        "vision.observed_at": vision.observed_at,
        "vision.frame_captured_at": vision.frame_captured_at,
        "infra.observed_at": infra.observed_at,
    }
    for name, ts in stamps.items():
        if ts > future:
            raise IncompleteEvidence(f"valid: {name} {ts.isoformat()} is in the future")
        if ts < stale:
            raise IncompleteEvidence(
                f"valid: {name} {ts.isoformat()} is older than {EVIDENCE_MAX_AGE_SECONDS}s"
            )
    if vision.observed_at < vision.frame_captured_at - timedelta(seconds=CLOCK_SKEW_SECONDS):
        raise IncompleteEvidence(
            "valid: vision.observed_at is before vision.frame_captured_at"
        )


def _build_summary(
    vision: VisionFinding,
    infra: InfraFinding,
    agreed: FaultClass,
    agreement: bool,
    overall: float,
) -> str:
    top_metrics = ", ".join(
        f"{k} {v}" for k, v in list(infra.supporting_metrics.items())[:4]
    )
    corr = (
        "Video symptom corroborates the telemetry fault class."
        if agreement
        else f"Video symptom {vision.symptom.value} carries no telemetry signature; "
        f"infra classification not corroborated by vision."
    )
    desc = vision.description.rstrip(". ")
    return (
        f"{agreed.value} on {infra.affected_component}. "
        f"Infra (conf {infra.confidence}): {top_metrics}. "
        f"Vision (conf {vision.confidence}): {vision.symptom.value} -- {desc}. "
        f"{corr} Overall evidence confidence {overall}."
    )


# --------------------------------------------------------------------------- #
# Manual run -- against one incident_id in Firestore
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys

    from incident_cli import looks_like_incident_id, run_step
    from models import IncidentStatus

    if len(sys.argv) > 1 and looks_like_incident_id(sys.argv[1]):
        # reads inc.vision + inc.infra, writes inc.evidence (or a STOP reason)
        run_step(
            sys.argv[1], step_name="aggregate", status=IncidentStatus.AGGREGATING,
            requires=("vision", "infra"), produces="evidence",
            compute=lambda inc: aggregate(inc.incident_id, inc.vision, inc.infra),
        )
    else:
        print("usage: python3 aggregator.py <incident_id>")
        print("       (runs the deterministic Aggregator against that incident's "
              "vision + infra findings in Firestore)")
        sys.exit(2)
