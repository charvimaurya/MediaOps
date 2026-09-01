"""
Check for aggregator.py -- pure, no network, no Gemini.

    python3 test_aggregator.py
"""

from datetime import datetime, timedelta, timezone

import aggregator
from aggregator import EvidenceConflict, IncompleteEvidence, aggregate
from models import (
    FaultClass,
    IncidentEvidence,
    InfraFinding,
    VisionFinding,
    VisionSymptom,
)

UTC = timezone.utc
INC = "inc-test-0001"


def vision(symptom=VisionSymptom.MACROBLOCKING, conf=0.95, **over):
    now = datetime.now(UTC)
    kw = dict(
        frame_captured_at=now - timedelta(seconds=5),
        observed_at=now,
        symptom=symptom,
        description="severe blocky compression artifacts across the frame",
        confidence=conf,
        model="gemini-2.5-flash",
        raw_response='{"symptom": "MACROBLOCKING", "confidence": 0.95}',
    )
    kw.update(over)
    return VisionFinding(**kw)


def infra(fault=FaultClass.ENCODER_OVERLOAD, conf=0.9, **over):
    kw = dict(
        observed_at=datetime.now(UTC),
        fault_class=fault,
        affected_component="encoder_01",
        description="cpu 97, fps 18, latency 190",
        supporting_metrics={"media_cpu_usage_percent": 97.0, "media_fps": 18.0},
        confidence=conf,
        model="gemini-2.5-flash",
        raw_response='{"fault_class": "encoder_overload", "confidence": 0.9}',
    )
    kw.update(over)
    return InfraFinding(**kw)


def expect_raise(exc_type, fn, *a, **k):
    try:
        fn(*a, **k)
    except exc_type as e:
        return str(e)
    raise AssertionError(f"expected {exc_type.__name__}, nothing raised")


# ---- 1. happy path (agree) --------------------------------------------- #
print("1. MACROBLOCKING + encoder_overload -> agreeing IncidentEvidence")
ev = aggregate(INC, vision(), infra())
assert isinstance(ev, IncidentEvidence)
assert ev.agreement is True
assert ev.fault_class is FaultClass.ENCODER_OVERLOAD
assert ev.confidence == min(0.95, 0.9), ev.confidence
assert ev.validation_passed is True and ev.validation_errors == []
assert "corroborates" in ev.summary
assert "encoder_overload" in ev.summary and "MACROBLOCKING" in ev.summary
print(f"   OK: fault_class={ev.fault_class.value} agreement={ev.agreement} confidence={ev.confidence}")
print(f"   summary: {ev.summary}")

# ---- 2/3. missing findings ------------------------------------------ #
print("\n2. missing vision -> IncompleteEvidence")
msg = expect_raise(IncompleteEvidence, aggregate, INC, None, infra())
assert "vision" in msg
print(f"   OK: {msg}")

print("\n3. missing infra -> IncompleteEvidence")
msg = expect_raise(IncompleteEvidence, aggregate, INC, vision(), None)
assert "infra" in msg
print(f"   OK: {msg}")

# ---- 4. infra with no metrics ---------------------------------- #
print("\n4. infra.supporting_metrics empty -> IncompleteEvidence")
msg = expect_raise(IncompleteEvidence, aggregate, INC, vision(), infra(supporting_metrics={}))
assert "supporting_metrics" in msg
print(f"   OK: {msg}")

# ---- 5. raw_response missing ------------------------------- #
print("\n5. raw_response=None on a finding -> IncompleteEvidence")
msg = expect_raise(IncompleteEvidence, aggregate, INC, vision(raw_response=None), infra())
assert "raw_response" in msg
print(f"   OK: {msg}")

# ---- 6. bad timestamps -------------------------------- #
print("\n6. stale / future timestamps -> IncompleteEvidence")
old = datetime.now(UTC) - timedelta(hours=2)
msg = expect_raise(IncompleteEvidence, aggregate, INC, vision(), infra(observed_at=old))
assert "older than" in msg
future = datetime.now(UTC) + timedelta(hours=1)
msg = expect_raise(IncompleteEvidence, aggregate, INC, vision(observed_at=future), infra())
assert "future" in msg
print(f"   OK: both rejected  (e.g. {msg})")

# ---- 7. conflict -------------------------------- #
print("\n7. BLACK_FRAME + network_degradation -> EvidenceConflict (no evidence produced)")
result = {"ev": None}
try:
    result["ev"] = aggregate(INC, vision(symptom=VisionSymptom.BLACK_FRAME),
                             infra(fault=FaultClass.NETWORK_DEGRADATION))
except EvidenceConflict as e:
    msg = str(e)
assert result["ev"] is None, "an IncidentEvidence leaked out on a conflict"
assert "BLACK_FRAME" in msg and "network_degradation" in msg
print(f"   OK: {msg}")

# ---- 8. abstain (vision) ---------------------------- #
print("\n8. NORMAL + encoder_overload -> proceeds, agreement=False, confidence halved")
ev = aggregate(INC, vision(symptom=VisionSymptom.NORMAL), infra(conf=0.8))
assert isinstance(ev, IncidentEvidence)
assert ev.agreement is False
assert ev.fault_class is FaultClass.ENCODER_OVERLOAD
assert ev.confidence == round(min(0.95, 0.8) * 0.5, 3), ev.confidence
assert "not corroborated" in ev.summary
print(f"   OK: agreement={ev.agreement} confidence={ev.confidence}")

# ---- 9. abstain (infra) ---------------------------- #
print("\n9. MACROBLOCKING + infra unknown -> proceeds, agreement=False")
ev = aggregate(INC, vision(), infra(fault=FaultClass.UNKNOWN))
assert ev.agreement is False
assert ev.fault_class is FaultClass.UNKNOWN
print(f"   OK: agreement={ev.agreement} fault_class={ev.fault_class.value}")

# ---- 10. no external deps ------------------------ #
print("\n10. aggregator.py imports nothing external (no AI, no I/O)")
src = open(aggregator.__file__).read()
for bad in ("import google", "import urllib", "import mcp", "import asyncio", "import requests",
            "from google", "from mcp"):
    assert bad not in src, f"aggregator.py contains {bad!r}"
print("   OK: pure stdlib + models.py")

print("\nALL CHECKS PASSED")
