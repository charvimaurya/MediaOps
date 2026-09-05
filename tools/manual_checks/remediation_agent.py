"""
Checks for remediation_agent.py.

    python3 -m tools.seed_knowledge_base     # once, first
    python3 -m tools.manual_checks.remediation_agent

Real Gemini calls for the happy paths (checks 2-4); the guardrails (5-8) run
offline with a monkey-patched _call_gemini. Checks 9-10 are static/structural.

Contract under test:
  - valid RemediationProposal on success (action always in the 4-member enum);
  - None after 2 invalid structured outputs (caller stops, no proposal);
  - raises on infra failure (Gemini error / empty response);
  - proposes only -- no execution, no infrastructure, no control.py.
"""

from datetime import datetime, timezone

from agents import remediation_agent
from agents.remediation_agent import _RemediationResponse, propose_remediation
import knowledge_base
from models import (
    FaultClass,
    IncidentEvidence,
    InfraFinding,
    KBMatch,
    RemediationAction,
    RemediationProposal,
    VisionFinding,
    VisionSymptom,
)
from pydantic import ValidationError

UTC = timezone.utc

OVERLOAD = ("encoder_overload on encoder_01. Infra (conf 0.95): media_cpu_usage_percent 97.0, "
            "media_fps 18.0, media_encoding_latency_ms 190.0. Vision (conf 0.95): MACROBLOCKING "
            "-- severe blocky compression artifacts across the entire frame. Video symptom "
            "corroborates the telemetry fault class. Overall evidence confidence 0.95.")
FAILURE = ("encoder_failure on encoder_01. Infra (conf 0.95): media_encoder_status 0.0, "
           "media_fps 0.0, media_bitrate_mbps 0.0, media_dropped_frames_percent 100.0. Vision "
           "(conf 1.0): BLACK_FRAME -- the screen is entirely black. Video symptom corroborates "
           "the telemetry fault class. Overall evidence confidence 0.95.")
NETWORK = ("network_degradation on network path. Infra (conf 0.9): media_packet_loss_percent "
           "14.0, media_network_latency_ms 480.0, media_fps 16.0. Vision (conf 0.8): NORMAL -- "
           "picture looks fine. Video symptom NORMAL carries no telemetry signature. Overall "
           "evidence confidence 0.4.")


def evidence(summary, *, fault_class=FaultClass.ENCODER_OVERLOAD,
             symptom=VisionSymptom.MACROBLOCKING, agreement=True) -> IncidentEvidence:
    now = datetime.now(UTC)
    return IncidentEvidence(
        incident_id="test",
        vision=VisionFinding(frame_captured_at=now, observed_at=now, symptom=symptom,
                             description="x", confidence=0.9, model="gemini-2.5-flash",
                             raw_response="{}"),
        infra=InfraFinding(observed_at=now, fault_class=fault_class,
                           affected_component="encoder_01", description="x",
                           supporting_metrics={"media_fps": 18.0, "media_cpu_usage_percent": 97.0},
                           confidence=0.9, model="gemini-2.5-flash", raw_response="{}"),
        agreement=agreement, fault_class=fault_class, confidence=0.9,
        summary=summary, validation_passed=True,
    )


def patch_call(fake):
    """Context-manager-ish: returns a restore callable."""
    real = remediation_agent._call_gemini
    remediation_agent._call_gemini = fake
    return lambda: setattr(remediation_agent, "_call_gemini", real)


# ---- 1. _RemediationResponse -- the enum guardrail ------------------ #
print("1. _RemediationResponse strict validation")
for a in ("RESTART_ENCODER", "REDUCE_PROFILE", "SWITCH_SOURCE", "FAILOVER"):
    r = _RemediationResponse.model_validate_json(
        f'{{"action":"{a}","rationale":"ok","confidence":0.7}}')
    assert r.action.value == a
for bad, why in [
    ('{"action":"REBOOT_EVERYTHING","rationale":"x","confidence":0.7}', "non-enum action"),
    ('{"action":"RESTART_ENCODER","rationale":"x","confidence":1.5}', "confidence > 1"),
    ('{"action":"RESTART_ENCODER","rationale":"","confidence":0.7}', "empty rationale"),
    ('{"action":"RESTART_ENCODER","rationale":"x","confidence":0.7,"extra":1}', "extra key"),
]:
    try:
        _RemediationResponse.model_validate_json(bad)
        raise AssertionError(f"expected rejection: {why}")
    except ValidationError:
        pass
print("   OK: 4 enum values accepted; non-enum / out-of-range / empty / extra rejected")

# ---- 2. real Gemini: encoder_overload + real KB precedent ---------- #
print("\n2. propose_remediation(encoder_overload evidence + real KB precedent)")
ev = evidence(OVERLOAD)
prec = knowledge_base.retrieve(ev)
assert prec and all(isinstance(m, KBMatch) for m in prec), "expected seeded precedent"
p = propose_remediation(ev, prec)
assert isinstance(p, RemediationProposal)
assert isinstance(p.action, RemediationAction)
assert p.rationale.strip()
assert 0.0 <= p.confidence <= 1.0
assert p.similar_incident_ids == [m.kb_id for m in prec]
assert p.model.startswith("gemini")
assert p.precedent_summary is not None
assert p.incident_id == "test"
print(p.model_dump_json(indent=2))
print(f"   OK: action={p.action.value} confidence={p.confidence}")

# ---- 3. real Gemini: encoder_failure + precedent ------------------ #
print("\n3. propose_remediation(encoder_failure evidence + precedent)")
ev = evidence(FAILURE, fault_class=FaultClass.ENCODER_FAILURE, symptom=VisionSymptom.BLACK_FRAME)
prec = knowledge_base.retrieve(ev)
p = propose_remediation(ev, prec)
assert isinstance(p, RemediationProposal) and isinstance(p.action, RemediationAction)
print(f"   OK: action={p.action.value} confidence={p.confidence}  ids={p.similar_incident_ids}")
print(f"        rationale: {p.rationale}")

# ---- 4. real Gemini: NO precedent -------------------------------- #
print("\n4. propose_remediation(network evidence, precedent=[])")
ev = evidence(NETWORK, fault_class=FaultClass.NETWORK_DEGRADATION,
              symptom=VisionSymptom.NORMAL, agreement=False)
p = propose_remediation(ev, [])
assert isinstance(p, RemediationProposal) and isinstance(p.action, RemediationAction)
assert p.similar_incident_ids == []
assert p.precedent_summary is None
print(f"   OK: decided from evidence alone -> action={p.action.value} confidence={p.confidence}")
print(f"        rationale: {p.rationale}")

# ---- 5. enum guardrail: invented action -> None ----------------- #
print("\n5. Gemini keeps returning a non-enum action -> None after 2 attempts")
calls = {"n": 0}
def fake_bad_action(context, instruction):
    calls["n"] += 1
    return '{"action":"REBOOT_EVERYTHING","rationale":"just reboot it all","confidence":0.9}'
restore = patch_call(fake_bad_action)
try:
    out = propose_remediation(evidence(OVERLOAD), [])
    assert out is None, f"a non-enum action leaked through: {out}"
    assert calls["n"] == 2, calls["n"]
finally:
    restore()
print(f"   OK: {calls['n']} attempts, returned None -- invented action never became a proposal")

# ---- 6. bad JSON -> None --------------------------------------- #
print("\n6. Gemini returns non-JSON -> None after 2 attempts")
calls = {"n": 0}
def fake_garbage(context, instruction):
    calls["n"] += 1
    return "sure, I'd restart the encoder here."
restore = patch_call(fake_garbage)
try:
    assert propose_remediation(evidence(OVERLOAD), []) is None
    assert calls["n"] == 2
finally:
    restore()
print("   OK: None")

# ---- 7. retry recovery ---------------------------------------- #
print("\n7. garbage on call 1, valid JSON on call 2 -> proposal")
calls = {"n": 0}
def fake_recover(context, instruction):
    calls["n"] += 1
    if calls["n"] == 1:
        return "hmm let me think"
    return '{"action":"RESTART_ENCODER","rationale":"overload; kb-0001 resolved by restart","confidence":0.8}'
restore = patch_call(fake_recover)
try:
    p = propose_remediation(evidence(OVERLOAD), [])
    assert isinstance(p, RemediationProposal)
    assert p.action is RemediationAction.RESTART_ENCODER
    assert calls["n"] == 2
finally:
    restore()
print("   OK: recovered on the retry")

# ---- 8. infra failure propagates (not None) ------------------ #
print("\n8. _call_gemini raises -> propose_remediation propagates")
def fake_raise(context, instruction):
    raise RuntimeError("simulated Vertex outage")
restore = patch_call(fake_raise)
try:
    propose_remediation(evidence(OVERLOAD), [])
    raise AssertionError("expected RuntimeError to propagate")
except RuntimeError as e:
    assert "outage" in str(e)
finally:
    restore()
print("   OK: raised (fail-closed at the caller), did not return None")

# ---- 9. proposes only -- no execution surface --------------- #
print("\n9. remediation_agent.py has no execution / infrastructure surface")
src = open(remediation_agent.__file__).read()
for bad in ("import simulator", "from simulator", "import control", "from control",
            "import subprocess", "import requests", "import urllib", "http.client",
            "restart_encoder", "reduce_bitrate", "switch_backup", "PipelineControl"):
    assert bad not in src, f"remediation_agent.py contains {bad!r}"
assert "tools=" not in src, "the agent must not be given tools (no function-calling)"
assert "disallow_transfer_to_parent=True" in src
print("   OK: no control.py, no subprocess/http, no tools= on the agent")

# ---- 10. precedent is an input -> no KB/network side effects  #
print("\n10. propose_remediation takes precedent as an argument (no retrieval inside)")
import inspect
sig = inspect.signature(propose_remediation)
assert list(sig.parameters) == ["evidence", "precedent"], list(sig.parameters)
body = inspect.getsource(propose_remediation)
assert "retrieve(" not in body and "knowledge_base" not in body
print("   OK: propose_remediation(evidence, precedent) -- no embedding/Firestore calls of its own")

print("\nALL CHECKS PASSED")
