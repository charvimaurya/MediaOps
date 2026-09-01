"""
Knowledge Base + RAG -- Vertex AI embeddings + plain-Python cosine similarity.

Part A (seeding): pre-seed Firestore with a handful of past resolved incidents,
each with a PRECOMPUTED embedding. Run once via `seed_knowledge_base.py`.

Part B (retrieval): given a new incident's `IncidentEvidence`, embed its summary
with Vertex AI, cosine-rank the seeded records in pure Python, and return only
the matches at or above a relevance threshold -- as PRECEDENT CONTEXT for the
Remediation Agent, never a decision.

Guardrails:
  - real Vertex embeddings at runtime (`_embed`)
  - deterministic cosine similarity (`_cosine`, pure Python)
  - relevance-threshold gate: weak matches are discarded, not returned; [] if none
  - precedent only -- `retrieve()` never picks or authorises an action, never
    writes anything, has no side effects.

Contract of `retrieve()`:
  - returns list[KBMatch] (possibly empty) on success;
  - raises RuntimeError if the KB is empty (run the seeder) or the embedding
    call fails -- fail-closed at the caller.

Standalone: `python3 knowledge_base.py`   (retrieval demo)
            `python3 seed_knowledge_base.py`   (one-time seed)
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timezone
from typing import Optional

from google import genai
from google.cloud import firestore
from google.genai import types

from models import IncidentEvidence, KBMatch, RemediationAction

logger = logging.getLogger("knowledge_base")

# --------------------------------------------------------------------------- #
# Config -- module constants, os.environ with defaults (matches infra_agent.py)
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
FIRESTORE_DATABASE_ID = os.environ.get("FIRESTORE_DATABASE_ID", "(default)")
KB_COLLECTION = os.environ.get("FIRESTORE_KB_COLLECTION", "knowledge_base")
EMBED_MODEL = os.environ.get("EMBED_MODEL", "text-embedding-005")
# 0.80 cleanly separates same-fault precedent (~0.82-0.88) from different-fault
# same-domain text (~0.71-0.80) and faults with no seeded precedent (< ~0.72).
RELEVANCE_THRESHOLD = float(os.environ.get("KB_RELEVANCE_THRESHOLD", "0.80"))
MAX_MATCHES = int(os.environ.get("KB_MAX_MATCHES", "3"))

_client: Optional[genai.Client] = None


def _genai() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(
            vertexai=True, project=GCP_PROJECT_ID, location=VERTEX_LOCATION
        )
    return _client


# --------------------------------------------------------------------------- #
# Embedding + cosine -- the two primitives
# --------------------------------------------------------------------------- #

def _embed(text: str, *, task_type: str) -> list[float]:
    """
    One real Vertex AI embedding call. `task_type` is 'RETRIEVAL_DOCUMENT' when
    seeding, 'RETRIEVAL_QUERY' when retrieving. Returns a 768-float, L2-normalised,
    deterministic vector. This is the single seam a test monkey-patches.
    """
    resp = _genai().models.embed_content(
        model=EMBED_MODEL,
        contents=text,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return list(resp.embeddings[0].values)


def _embed_many(texts: list[str], *, task_type: str) -> list[list[float]]:
    resp = _genai().models.embed_content(
        model=EMBED_MODEL,
        contents=texts,
        config=types.EmbedContentConfig(task_type=task_type),
    )
    return [list(e.values) for e in resp.embeddings]


def _cosine(a: list[float], b: list[float]) -> float:
    """Deterministic cosine similarity. 0.0 on empty / mismatched / zero-norm inputs."""
    if not a or len(a) != len(b):
        return 0.0
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


# --------------------------------------------------------------------------- #
# Part A -- seeding
# --------------------------------------------------------------------------- #

# Summaries are written in aggregator._build_summary()'s style so their embeddings
# land near a real IncidentEvidence.summary for the same fault.
SEED_RECORDS: list[dict] = [
    {
        "kb_id": "kb-0001",
        "fault_class": "encoder_overload",
        "action_taken": "RESTART_ENCODER",
        "outcome": "resolved",
        "recovery_seconds": 45.0,
        "summary": (
            "encoder_overload on encoder_01. Infra (conf 0.95): media_cpu_usage_percent 97.0, "
            "media_fps 18.0, media_encoding_latency_ms 190.0. Vision (conf 0.95): MACROBLOCKING "
            "-- heavy blocky compression artifacts across the whole frame. Video symptom "
            "corroborates the telemetry fault class. Resolved by RESTART_ENCODER in 45s."
        ),
    },
    {
        "kb_id": "kb-0002",
        "fault_class": "encoder_overload",
        "action_taken": "REDUCE_PROFILE",
        "outcome": "resolved",
        "recovery_seconds": 30.0,
        "summary": (
            "encoder_overload on encoder_01. Infra (conf 0.9): media_cpu_usage_percent 94.0, "
            "media_fps 21.0, media_encoding_latency_ms 165.0. Vision (conf 0.85): MACROBLOCKING "
            "-- moderate blockiness and smearing on motion. Video symptom corroborates the "
            "telemetry fault class. Resolved by REDUCE_PROFILE (lower bitrate) in 30s."
        ),
    },
    {
        "kb_id": "kb-0003",
        "fault_class": "encoder_failure",
        "action_taken": "RESTART_ENCODER",
        "outcome": "resolved",
        "recovery_seconds": 52.0,
        "summary": (
            "encoder_failure on encoder_01. Infra (conf 0.97): media_encoder_status 0.0, "
            "media_fps 0.0, media_bitrate_mbps 0.0, media_dropped_frames_percent 100.0. Vision "
            "(conf 1.0): BLACK_FRAME -- screen entirely black, no picture. Video symptom "
            "corroborates the telemetry fault class. Resolved by RESTART_ENCODER in 52s."
        ),
    },
    {
        "kb_id": "kb-0004",
        "fault_class": "encoder_failure",
        "action_taken": "SWITCH_SOURCE",
        "outcome": "resolved",
        "recovery_seconds": 70.0,
        "summary": (
            "encoder_failure on encoder_01. Infra (conf 0.95): media_encoder_status 0.0, "
            "media_fps 0.0, media_dropped_frames_percent 100.0. Vision (conf 1.0): BLACK_FRAME "
            "-- black output, stream down. RESTART_ENCODER did not bring the encoder back; "
            "resolved by SWITCH_SOURCE to the backup encoder in 70s."
        ),
    },
    {
        "kb_id": "kb-0005",
        "fault_class": "rgb_shift",
        "action_taken": "RESTART_ENCODER",
        "outcome": "resolved",
        "recovery_seconds": 40.0,
        "summary": (
            "rgb_shift on encoder_01. Infra (conf 0.4): media_pipeline_health 0.0, telemetry "
            "otherwise nominal. Vision (conf 0.95): RGB_SHIFT -- persistent red and cyan colour "
            "fringing and channel misregistration on every edge. Video symptom carries no "
            "telemetry signature; not corroborated by infra. Resolved by RESTART_ENCODER "
            "(re-aligned the colour channels) in 40s."
        ),
    },
    {
        "kb_id": "kb-0006",
        "fault_class": "rgb_shift",
        "action_taken": "SWITCH_SOURCE",
        "outcome": "not_resolved",
        "recovery_seconds": 0.0,
        "summary": (
            "rgb_shift on encoder_01. Infra (conf 0.3): telemetry nominal, media_pipeline_health "
            "flapping. Vision (conf 0.9): RGB_SHIFT -- red and blue channels offset from green, "
            "chromatic aberration throughout the frame. SWITCH_SOURCE did not clear the shift; "
            "incident escalated to a human, not resolved."
        ),
    },
]


def _db(client: Optional[firestore.Client] = None) -> firestore.Client:
    return client or firestore.Client(project=GCP_PROJECT_ID, database=FIRESTORE_DATABASE_ID)


def seed(client: Optional[firestore.Client] = None) -> int:
    """
    Embed every SEED_RECORD's summary and write it to KB_COLLECTION with kb_id as
    the doc id (idempotent -- safe to re-run). Returns the number of records seeded.
    """
    col = _db(client).collection(KB_COLLECTION)
    vectors = _embed_many([r["summary"] for r in SEED_RECORDS], task_type="RETRIEVAL_DOCUMENT")
    now = datetime.now(timezone.utc).isoformat()

    for record, vector in zip(SEED_RECORDS, vectors):
        doc = {
            **record,
            "embedding": vector,
            "embedding_model": EMBED_MODEL,
            "embedding_task_type": "RETRIEVAL_DOCUMENT",
            "seeded_at": now,
        }
        col.document(record["kb_id"]).set(doc)
        logger.info(
            "seeded %s  fault=%s  action=%s  outcome=%s  (embedding dim %d)",
            record["kb_id"], record["fault_class"], record["action_taken"],
            record["outcome"], len(vector),
        )
    return len(SEED_RECORDS)


# --------------------------------------------------------------------------- #
# Part B -- retrieval
# --------------------------------------------------------------------------- #

def _load_kb(client: Optional[firestore.Client] = None) -> list[dict]:
    return [snap.to_dict() for snap in _db(client).collection(KB_COLLECTION).stream()]


def score_kb(query_summary: str, *, client: Optional[firestore.Client] = None) -> list[tuple[dict, float]]:
    """
    Every KB record paired with its cosine similarity to `query_summary`, sorted
    high to low. No threshold applied -- useful for tuning / diagnostics.
    """
    records = _load_kb(client)
    if not records:
        raise RuntimeError(
            "knowledge base is empty -- run: python3 seed_knowledge_base.py"
        )
    q = _embed(query_summary, task_type="RETRIEVAL_QUERY")
    scored = [(rec, _cosine(q, rec.get("embedding", []))) for rec in records]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored


def retrieve(
    evidence: IncidentEvidence,
    *,
    client: Optional[firestore.Client] = None,
) -> list[KBMatch]:
    """
    Precedent for the Remediation Agent: the past incidents most similar to this
    one, filtered to those at or above RELEVANCE_THRESHOLD. Empty list if none
    clear the bar (no weak matches). Never chooses or authorises an action.
    """
    scored = score_kb(evidence.summary, client=client)
    kept = [(rec, sim) for rec, sim in scored if sim >= RELEVANCE_THRESHOLD][:MAX_MATCHES]

    logger.info(
        "kb retrieve: %d records, %d >= threshold %.2f -> %s",
        len(scored), len(kept), RELEVANCE_THRESHOLD, [r["kb_id"] for r, _ in kept],
    )

    return [
        KBMatch(
            kb_id=rec["kb_id"],
            fault_class=rec["fault_class"],
            action_taken=RemediationAction(rec["action_taken"]),
            outcome=rec["outcome"],
            similarity=round(sim, 4),
            summary=rec["summary"],
        )
        for rec, sim in kept
    ]


# --------------------------------------------------------------------------- #
# Manual run -- retrieval demo
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    demo_summary = (
        "encoder_overload on encoder_01. Infra (conf 0.95): media_cpu_usage_percent 97.0, "
        "media_fps 18.0, media_encoding_latency_ms 190.0. Vision (conf 0.95): MACROBLOCKING "
        "-- severe blocky compression artifacts across the entire frame. Video symptom "
        "corroborates the telemetry fault class. Overall evidence confidence 0.95."
    )

    print("query:\n ", demo_summary, "\n")
    print(f"all KB records by similarity (threshold {RELEVANCE_THRESHOLD}):")
    for rec, sim in score_kb(demo_summary):
        mark = ">=" if sim >= RELEVANCE_THRESHOLD else "  "
        print(f"  {mark} {sim:.4f}  {rec['kb_id']}  {rec['fault_class']}/{rec['action_taken']}")

    from models import InfraFinding, VisionFinding, VisionSymptom, FaultClass

    now = datetime.now(timezone.utc)
    evidence = IncidentEvidence(
        incident_id="demo",
        vision=VisionFinding(frame_captured_at=now, observed_at=now,
                             symptom=VisionSymptom.MACROBLOCKING, description="blocky",
                             confidence=0.95, model="gemini-2.5-flash", raw_response="{}"),
        infra=InfraFinding(observed_at=now, fault_class=FaultClass.ENCODER_OVERLOAD,
                           affected_component="encoder_01", description="cpu 97 fps 18",
                           supporting_metrics={"media_cpu_usage_percent": 97.0}, confidence=0.95,
                           model="gemini-2.5-flash", raw_response="{}"),
        agreement=True, fault_class=FaultClass.ENCODER_OVERLOAD, confidence=0.95,
        summary=demo_summary, validation_passed=True,
    )
    print("\nretrieve() ->")
    for m in retrieve(evidence):
        print("  " + m.model_dump_json())
