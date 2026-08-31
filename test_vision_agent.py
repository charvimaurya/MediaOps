"""
Check for vision_agent.py. Makes a REAL Gemini call (Vertex AI) for check 3.

    python3 test_vision_agent.py

Prereqs: simulator running (writing simulator/output/primary/), ffmpeg on PATH,
ADC configured, Vertex AI enabled.

Covers: frame selection, frame extraction, one real analysis, and the strict
structured-output guardrail (bad JSON / bad enum -> one retry -> None; and a
retry that recovers).
"""

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import vision_agent
from models import VisionFinding, VisionSymptom


def probe_dims(path: Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


# ---- 1. latest_segment --------------------------------------------------- #
print("1. latest_segment() finds a real completed HLS segment")
seg = vision_agent.latest_segment(vision_agent.PRIMARY_OUTPUT_DIR)
assert seg.exists() and seg.suffix == ".ts", seg
assert str(seg).startswith(vision_agent.PRIMARY_OUTPUT_DIR), seg
print(f"   OK: {seg.name}")

# ---- 2. extract_frames ------------------------------------------------- #
print("\n2. extract_frames() pulls 3 readable 320x180 JPEGs")
wd = Path(tempfile.mkdtemp(prefix="vision_test_"))
try:
    frames = vision_agent.extract_frames(seg, 3, wd)
    assert len(frames) == 3, frames
    for f in frames:
        assert f.stat().st_size > 1000, (f, f.stat().st_size)
        assert probe_dims(f) == "320,180", probe_dims(f)
    print(f"   OK: {[f.name for f in frames]}  sizes={[f.stat().st_size for f in frames]}")
finally:
    for f in wd.glob("*"):
        f.unlink()
    wd.rmdir()

# ---- 3. real analysis -------------------------------------------------- #
# NOTE: the simulator's healthy source video is a colour-bars test card with a
# countdown digit -- so the correct viewer-visible finding on a HEALTHY stream is
# COLOR_BARS, not NORMAL. Encoder faults in this sim are telemetry-only and don't
# change the primary video, so vision stays COLOR_BARS through them (honest).
print("\n3. analyze_frame() -- REAL Gemini call (healthy stream = colour-bars card)")
finding = vision_agent.analyze_frame()
assert isinstance(finding, VisionFinding), type(finding)
assert finding.source == "vision"
assert isinstance(finding.symptom, VisionSymptom)
assert 0.0 <= finding.confidence <= 1.0
assert finding.model.startswith("gemini"), finding.model
age = (datetime.now(timezone.utc) - finding.frame_captured_at).total_seconds()
assert 0 <= age < 600, f"frame_captured_at looks wrong (age {age:.0f}s)"
assert finding.raw_response and json.loads(finding.raw_response)  # parses as JSON
print("   " + finding.model_dump_json(indent=2).replace("\n", "\n   "))
assert finding.symptom in {VisionSymptom.COLOR_BARS, VisionSymptom.NORMAL}, (
    f"expected COLOR_BARS/NORMAL on the healthy test card, got {finding.symptom.value}"
)
print(f"   OK: healthy stream -> {finding.symptom.value} (confidence {finding.confidence})")

# ---- 4/5/6. strict structured-output guardrail ----------------------- #
_real_call = vision_agent._call_gemini


def patched(sequence):
    """Return canned responses in order; count calls."""
    calls = {"n": 0}

    def fake(frames, instruction):
        i = calls["n"]
        calls["n"] += 1
        return sequence[min(i, len(sequence) - 1)]

    return fake, calls


VALID = '{"symptom": "NORMAL", "description": "looks fine", "confidence": 0.9}'

try:
    print("\n4. bad JSON twice -> analyze_frame() returns None after 1 retry")
    vision_agent._call_gemini, calls = patched(["not json at all"])
    out = vision_agent.analyze_frame()
    assert out is None, out
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: None, {calls['n']} calls (1 try + 1 retry)")

    print("\n5. invalid enum twice -> None after 1 retry")
    vision_agent._call_gemini, calls = patched(
        ['{"symptom": "KINDA_BROKEN", "description": "x", "confidence": 0.5}']
    )
    out = vision_agent.analyze_frame()
    assert out is None, out
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: None, {calls['n']} calls")

    print("\n6. garbage then valid -> retry recovers, returns a VisionFinding")
    vision_agent._call_gemini, calls = patched(["{bad", VALID])
    out = vision_agent.analyze_frame()
    assert isinstance(out, VisionFinding), out
    assert out.symptom is VisionSymptom.NORMAL
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: VisionFinding on attempt 2 ({calls['n']} calls)")
finally:
    vision_agent._call_gemini = _real_call

print("\nALL CHECKS PASSED")
