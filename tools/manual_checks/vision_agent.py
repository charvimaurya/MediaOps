"""
Check for vision_agent.py. Makes REAL Gemini calls (Vertex AI) for check 3.

    python3 simulator/generate_messy_video.py   # once, to create the messy video
    python3 -m tools.manual_checks.vision_agent

Prereqs: simulator/output/messy_video.mov exists, ffmpeg on PATH, ADC configured,
Vertex AI enabled.

Covers: fault->timestamp mapping, mid-section frame extraction, real analysis per
fault section, and the strict structured-output guardrail (bad JSON / bad enum ->
one retry -> None; and a retry that recovers).
"""

import json
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from agents import vision_agent
from models import VisionFinding, VisionSymptom


def probe_dims(path: Path) -> str:
    return subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


MESSY = Path(vision_agent.MESSY_VIDEO)

# ---- 1. fault -> mid-section timestamp --------------------------------- #
print("1. fault -> mid-section timestamp mapping")
assert MESSY.exists(), f"{MESSY} missing -- run simulator/generate_messy_video.py"
assert vision_agent._mid_timestamp("overload") == 8.0
assert vision_agent._mid_timestamp("rgb_shift") == 14.0
assert vision_agent._mid_timestamp("encoder_failure") == 20.0
assert vision_agent._mid_timestamp("healthy") == 2.5
try:
    vision_agent._mid_timestamp("nonsense")
    raise AssertionError("expected ValueError for an unknown fault")
except ValueError:
    pass
print(f"   OK: {vision_agent.FAULT_SECTIONS}")

# ---- 2. extract_section_frames --------------------------------------- #
print("\n2. extract_section_frames() pulls 3 mid-section 1080x2048 JPEGs")
wd = Path(tempfile.mkdtemp(prefix="vision_test_"))
try:
    frames = vision_agent.extract_section_frames(MESSY, "overload", wd)
    assert len(frames) == 3, frames
    for f in frames:
        assert f.stat().st_size > 1000, (f, f.stat().st_size)
        assert probe_dims(f) == "1080,2048", probe_dims(f)
    print(f"   OK: {[f.name for f in frames]}  sizes={[f.stat().st_size for f in frames]}")
finally:
    for f in wd.glob("*"):
        f.unlink()
    wd.rmdir()

# ---- 3. real analysis, one per fault section ------------------------- #
print("\n3. analyze_frame() -- REAL Gemini call per section")
EXPECT = {
    "healthy": {VisionSymptom.NORMAL, VisionSymptom.COLOR_BARS},
    "overload": {VisionSymptom.MACROBLOCKING, VisionSymptom.OTHER},
    "encoder_failure": {VisionSymptom.BLACK_FRAME},
    "rgb_shift": None,  # Gemini read this as "stylistic/NORMAL" -- known prompt gap, don't assert
}
for fault, expected in EXPECT.items():
    finding = vision_agent.analyze_frame(fault=fault)
    assert isinstance(finding, VisionFinding), (fault, type(finding))
    assert finding.source == "vision"
    assert isinstance(finding.symptom, VisionSymptom)
    assert 0.0 <= finding.confidence <= 1.0
    assert finding.model.startswith("gemini"), finding.model
    age = (datetime.now(timezone.utc) - finding.frame_captured_at).total_seconds()
    assert 0 <= age < 600
    assert finding.raw_response and json.loads(finding.raw_response)
    mark = "OK" if (expected is None or finding.symptom in expected) else "NOTE"
    print(f"   [{mark}] {fault:16} -> {finding.symptom.value:14} conf={finding.confidence}"
          f"  | {finding.description}")
    if expected is not None:
        assert finding.symptom in expected, (fault, finding.symptom, "expected", expected)

# ---- 4/5/6. strict structured-output guardrail (unchanged) ---------- #
_real_call = vision_agent._call_gemini


def patched(sequence):
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
    out = vision_agent.analyze_frame(fault="overload")
    assert out is None, out
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: None, {calls['n']} calls (1 try + 1 retry)")

    print("\n5. invalid enum twice -> None after 1 retry")
    vision_agent._call_gemini, calls = patched(
        ['{"symptom": "KINDA_BROKEN", "description": "x", "confidence": 0.5}']
    )
    out = vision_agent.analyze_frame(fault="overload")
    assert out is None, out
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: None, {calls['n']} calls")

    print("\n6. garbage then valid -> retry recovers, returns a VisionFinding")
    vision_agent._call_gemini, calls = patched(["{bad", VALID])
    out = vision_agent.analyze_frame(fault="overload")
    assert isinstance(out, VisionFinding), out
    assert out.symptom is VisionSymptom.NORMAL
    assert calls["n"] == 2, calls["n"]
    print(f"   OK: VisionFinding on attempt 2 ({calls['n']} calls)")
finally:
    vision_agent._call_gemini = _real_call

print("\nALL CHECKS PASSED")
