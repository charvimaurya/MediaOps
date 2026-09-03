"""
Check for knowledge_base.py.

    python3 -m tools.seed_knowledge_base     # once, first
    python3 -m tests.test_knowledge_base

Prereqs: KB seeded in Firestore, ADC configured, Vertex AI enabled.
The retrieval checks make real Vertex embedding calls; the ranking / guardrail
logic is also checked offline with a monkey-patched _embed.
"""

from datetime import datetime, timezone

import knowledge_base as kb
from models import (
    FaultClass,
    IncidentEvidence,
    InfraFinding,
    KBMatch,
    RemediationAction,
    VisionFinding,
    VisionSymptom,
)

UTC = timezone.utc


def evidence(summary: str) -> IncidentEvidence:
    now = datetime.now(UTC)
    return IncidentEvidence(
        incident_id="test",
        vision=VisionFinding(frame_captured_at=now, observed_at=now,
                             symptom=VisionSymptom.MACROBLOCKING, description="x",
                             confidence=0.9, model="gemini-2.5-flash", raw_response="{}"),
        infra=InfraFinding(observed_at=now, fault_class=FaultClass.ENCODER_OVERLOAD,
                           affected_component="encoder_01", description="x",
                           supporting_metrics={"media_fps": 18.0}, confidence=0.9,
                           model="gemini-2.5-flash", raw_response="{}"),
        agreement=True, fault_class=FaultClass.ENCODER_OVERLOAD, confidence=0.9,
        summary=summary, validation_passed=True,
    )


OVERLOAD = ("encoder_overload on encoder_01. Infra (conf 0.95): media_cpu_usage_percent 97.0, "
            "media_fps 18.0, media_encoding_latency_ms 190.0. Vision (conf 0.95): MACROBLOCKING "
            "-- severe blocky compression artifacts across the entire frame. Video symptom "
            "corroborates the telemetry fault class. Overall evidence confidence 0.95.")
FAILURE = ("encoder_failure on encoder_01. Infra (conf 0.95): media_encoder_status 0.0, "
           "media_fps 0.0, media_bitrate_mbps 0.0, media_dropped_frames_percent 100.0. Vision "
           "(conf 1.0): BLACK_FRAME -- the screen is entirely black. Video symptom corroborates "
           "the telemetry fault class. Overall evidence confidence 0.95.")
RGB = ("unknown on encoder_01. Infra (conf 0.3): telemetry nominal. Vision (conf 0.95): "
       "RGB_SHIFT -- persistent red and cyan colour fringing and misregistration on every edge. "
       "Video symptom RGB_SHIFT carries no telemetry signature. Overall evidence confidence 0.29.")
NETWORK = ("network_degradation on network path. Infra (conf 0.9): media_packet_loss_percent "
           "14.0, media_network_latency_ms 480.0, media_fps 16.0. Vision (conf 0.8): NORMAL -- "
           "picture looks fine. Video symptom NORMAL carries no telemetry signature. Overall "
           "evidence confidence 0.4.")


# ---- 1. _cosine ------------------------------------------------------- #
print("1. _cosine -- pure, deterministic")
v = [0.1, -0.2, 0.3, 0.4]
assert abs(kb._cosine(v, v) - 1.0) < 1e-9
assert kb._cosine(v, [0.0, 0.0, 0.0, 0.0]) == 0.0
assert kb._cosine(v, [1.0, 2.0]) == 0.0            # length mismatch
assert kb._cosine([], []) == 0.0
assert kb._cosine(v, v) == kb._cosine(v, v)        # deterministic
assert abs(kb._cosine([1, 0], [0, 1])) < 1e-9      # orthogonal
print("   OK")

# ---- 2/3/4. real retrieval, per fault type ------------------------- #
print("\n2. retrieve(encoder_overload evidence) -- real Vertex embedding")
scored = kb.score_kb(OVERLOAD)
for rec, sim in scored:
    print(f"   {sim:.4f}  {rec['kb_id']}  {rec['fault_class']}/{rec['action_taken']}")
matches = kb.retrieve(evidence(OVERLOAD))
assert all(isinstance(m, KBMatch) for m in matches)
assert matches, "expected at least one match for a seeded fault type"
assert matches[0].kb_id in {"kb-0001", "kb-0002"}, matches[0].kb_id
assert matches[0].fault_class == "encoder_overload"
assert all(m.similarity >= kb.RELEVANCE_THRESHOLD for m in matches)
assert all(m.fault_class == "encoder_overload" for m in matches), [m.fault_class for m in matches]
print(f"   OK: {[ (m.kb_id, m.similarity) for m in matches ]}")

print("\n3. retrieve(encoder_failure evidence)")
matches = kb.retrieve(evidence(FAILURE))
assert matches and matches[0].kb_id in {"kb-0003", "kb-0004"}, [m.kb_id for m in matches]
assert all(m.fault_class == "encoder_failure" for m in matches), [m.fault_class for m in matches]
print(f"   OK: {[ (m.kb_id, m.similarity) for m in matches ]}")

print("\n4. retrieve(rgb_shift evidence)")
matches = kb.retrieve(evidence(RGB))
assert matches and matches[0].kb_id in {"kb-0005", "kb-0006"}, [m.kb_id for m in matches]
assert all(m.fault_class == "rgb_shift" for m in matches), [m.fault_class for m in matches]
print(f"   OK: {[ (m.kb_id, m.similarity) for m in matches ]}")

# ---- 5. threshold guardrail -------------------------------------- #
print("\n5. relevance-threshold guardrail")
scored = kb.score_kb(NETWORK)
print("   network query similarities:", [round(s, 3) for _, s in scored])
matches = kb.retrieve(evidence(NETWORK))
assert matches == [], f"weak matches leaked for a fault with no precedent: {[m.kb_id for m in matches]}"
print("   OK: no seeded precedent for network_degradation -> retrieve() returned []")

_thr = kb.RELEVANCE_THRESHOLD
try:
    kb.RELEVANCE_THRESHOLD = 0.99
    assert kb.retrieve(evidence(OVERLOAD)) == []
finally:
    kb.RELEVANCE_THRESHOLD = _thr
print("   OK: threshold 0.99 -> even the best match is discarded, [] returned")

# ---- 6. precedent only ---------------------------------------- #
print("\n6. precedent only -- no decision, no side effects")
before = sum(1 for _ in kb._db().collection(kb.KB_COLLECTION).stream())
out = kb.retrieve(evidence(OVERLOAD))
after = sum(1 for _ in kb._db().collection(kb.KB_COLLECTION).stream())
assert isinstance(out, list) and all(isinstance(m, KBMatch) for m in out)
assert not any(isinstance(getattr(m, "action_taken", None), RemediationAction) is False for m in out)
assert "RemediationProposal" not in {type(m).__name__ for m in out}
assert before == after, "retrieve() changed the KB collection"
print(f"   OK: returns list[KBMatch], KB unchanged ({before} docs before and after)")

# ---- 7. empty KB -------------------------------------------- #
print("\n7. empty KB -> RuntimeError")
_col = kb.KB_COLLECTION
try:
    kb.KB_COLLECTION = "knowledge_base_does_not_exist_xyz"
    kb.retrieve(evidence(OVERLOAD))
    raise AssertionError("expected RuntimeError for an empty KB")
except RuntimeError as e:
    assert "seed" in str(e)
    print(f"   OK: {e}")
finally:
    kb.KB_COLLECTION = _col

# ---- 8. offline ranking (monkey-patched _embed) ------------ #
print("\n8. ranking logic offline (monkey-patched _embed)")
_real = kb._embed
try:
    # query vector identical to kb-0001's stored embedding -> perfect match there
    recs = kb._load_kb()
    by_id = {r["kb_id"]: r for r in recs}
    kb._embed = lambda text, *, task_type: list(by_id["kb-0001"]["embedding"])
    m = kb.retrieve(evidence("anything"))
    assert m[0].kb_id == "kb-0001" and abs(m[0].similarity - 1.0) < 1e-6, (m[0].kb_id, m[0].similarity)
    assert len(m) <= kb.MAX_MATCHES
    print(f"   OK: identical-vector query -> kb-0001 at similarity {m[0].similarity}, "
          f"capped at MAX_MATCHES={kb.MAX_MATCHES}")
finally:
    kb._embed = _real

print("\nALL CHECKS PASSED")
