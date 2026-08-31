"""
The Vision Agent -- real Gemini (via Vertex AI, using Google ADK).

Given an incident, it grabs 3 recent stills from the simulator's live HLS output
(`simulator/output/primary/`), sends them to Gemini, and returns a schema-valid
`VisionFinding` describing the viewer-visible symptom.

Tier 1 guardrail: the structured-output check is real. Gemini's answer is parsed
strictly with Pydantic; if it doesn't validate, ONE strict retry; if it still
fails, we return None -- never a malformed or free-form finding.

Contract of `analyze_frame()`:
  - returns a valid VisionFinding on success;
  - returns None if Gemini's output fails validation twice
    -> the caller must STOP the diagnostic;
  - raises on infrastructure failure (no segments, ffmpeg error, Gemini
    API/auth/quota error) -- also fail-closed at the caller.

Standalone: `python3 vision_agent.py`
Prereqs: simulator running, ffmpeg on PATH, ADC configured, Vertex AI enabled.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from google.adk.agents import LlmAgent
from google.adk.models import Gemini
from google.adk.runners import InMemoryRunner
from google.genai import types

from models import Incident, VisionFinding, VisionSymptom

logger = logging.getLogger("vision_agent")

# --------------------------------------------------------------------------- #
# Config -- module constants, os.environ with defaults (matches detector.py)
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
VISION_MODEL = os.environ.get("VISION_MODEL", "gemini-2.5-flash")
PRIMARY_OUTPUT_DIR = os.environ.get(
    "PRIMARY_OUTPUT_DIR",
    os.path.join(os.path.dirname(__file__), "simulator", "output", "primary"),
)
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")

FRAME_COUNT = 3
MAX_ATTEMPTS = 2  # one call + one strict retry
_APP_NAME = "mediaops_vision_agent"


# --------------------------------------------------------------------------- #
# 1. Pull recent frames from the live HLS output (read-only)
# --------------------------------------------------------------------------- #

def latest_segment(output_dir: str) -> Path:
    """
    Newest *complete* HLS segment. The last `.ts` line in playlist.m3u8 is always
    a finished segment; fall back to the 2nd-newest file by name (the very newest
    may still be mid-write).
    """
    d = Path(output_dir)
    playlist = d / "playlist.m3u8"
    if playlist.exists():
        segs = [
            line.strip()
            for line in playlist.read_text().splitlines()
            if line.strip().endswith(".ts")
        ]
        if segs and (d / segs[-1]).exists():
            return d / segs[-1]

    files = sorted(glob.glob(os.path.join(output_dir, "segment_*.ts")))
    if len(files) < 2:
        raise FileNotFoundError(
            f"need >=2 HLS segments in {output_dir} -- is the simulator running?"
        )
    return Path(files[-2])


def extract_frames(segment: Path, n: int, workdir: Path) -> list[Path]:
    """
    Pull `n` evenly-spaced JPEG stills from one 2s / 24fps segment (48 frames).
    e.g. n=3 -> frames 0, 16, 32. Written into `workdir`.
    """
    step = max(1, 48 // n)
    out_pattern = str(workdir / "frame_%02d.jpg")
    cmd = [
        FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
        "-i", str(segment),
        "-vf", f"select='not(mod(n,{step}))'",
        "-fps_mode", "passthrough",
        "-frames:v", str(n),
        "-q:v", "3",
        "-y", out_pattern,
    ]
    subprocess.run(cmd, check=True, timeout=20)
    frames = sorted(workdir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError(f"ffmpeg produced no frames from {segment}")
    return frames


def _segment_captured_at(segment: Path) -> datetime:
    """When the simulator finished writing that segment (best proxy for capture time)."""
    return datetime.fromtimestamp(segment.stat().st_mtime, tz=timezone.utc)


# --------------------------------------------------------------------------- #
# 2. The ADK agent + the real Gemini call
# --------------------------------------------------------------------------- #

class _VisionResponse(BaseModel):
    """The exact JSON contract asked of Gemini. Validated strictly."""

    model_config = ConfigDict(extra="forbid")

    symptom: VisionSymptom
    description: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0)


_INSTRUCTION = """You are the read-only MediaOps Vision Agent for a live video stream.
You are shown 3 still frames captured about 1 second apart, in order. Judge ONLY
what a viewer would see on screen.

Classify the dominant viewer-visible symptom as EXACTLY ONE of:
- NORMAL: the picture looks fine -- normal moving video, nothing wrong on screen
- BLACK_FRAME: the picture is mostly or entirely black / no picture
- FROZEN_FRAME: the 3 frames are identical or barely change -- the video is stuck
- MACROBLOCKING: blocky compression artefacts, smearing, tearing, visible corruption
- COLOR_BARS: an SMPTE-style colour-bar test pattern
- SLATE: a "technical difficulties" / "please stand by" standby card
- OTHER: a clear visible problem that is none of the above

Important: if the picture looks fine, answer NORMAL with high confidence. An
encoder under heavy load often still looks completely normal in a short clip --
do NOT invent a problem that isn't visible.

Give a one-sentence `description` of what you actually see, and a `confidence`
between 0 and 1. Return ONLY the JSON object, no markdown, no commentary.
"""

_RETRY_SUFFIX = """

YOUR PREVIOUS RESPONSE FAILED VALIDATION: {error}
Return ONLY a JSON object with exactly these three keys and nothing else:
  "symptom": one of NORMAL, BLACK_FRAME, FROZEN_FRAME, MACROBLOCKING, COLOR_BARS, SLATE, OTHER
  "description": a short string
  "confidence": a number between 0 and 1
"""


def _build_agent(instruction: str) -> LlmAgent:
    return LlmAgent(
        name=_APP_NAME,
        model=Gemini(
            model=VISION_MODEL,
            client_kwargs={
                "vertexai": True,
                "project": GCP_PROJECT_ID,
                "location": VERTEX_LOCATION,
            },
        ),
        instruction=instruction,
        output_schema=_VisionResponse,
        include_contents="none",
        generate_content_config=types.GenerateContentConfig(temperature=0.0),
        disallow_transfer_to_parent=True,
        disallow_transfer_to_peers=True,
    )


def _call_gemini(frames: list[Path], instruction: str) -> str:
    """
    One real ADK runtime call to Gemini on Vertex AI. Returns the raw final text
    (JSON). This is the single seam a test monkey-patches.
    """
    agent = _build_agent(instruction)
    runner = InMemoryRunner(agent=agent, app_name=_APP_NAME)
    runner.session_service.create_session_sync(
        app_name=_APP_NAME, user_id="vision", session_id="vision"
    )

    parts = [
        types.Part.from_bytes(data=f.read_bytes(), mime_type="image/jpeg") for f in frames
    ]
    parts.append(
        types.Part.from_text(text=f"Analyse these {len(frames)} ordered frames (~1s apart).")
    )
    message = types.Content(role="user", parts=parts)

    final: Optional[str] = None
    for event in runner.run(user_id="vision", session_id="vision", new_message=message):
        if event.is_final_response() and event.content and event.content.parts:
            final = event.content.parts[0].text
    if not final:
        raise RuntimeError("Gemini returned no final response")
    return final


# --------------------------------------------------------------------------- #
# 3. analyze_frame -- orchestration + the strict guardrail
# --------------------------------------------------------------------------- #

def analyze_frame(
    incident: Optional[Incident] = None,
    *,
    output_dir: Optional[str] = None,
) -> Optional[VisionFinding]:
    """See module docstring for the contract."""
    output_dir = output_dir or PRIMARY_OUTPUT_DIR
    ctx = f" (incident {incident.incident_id})" if incident is not None else ""

    segment = latest_segment(output_dir)
    frame_captured_at = _segment_captured_at(segment)
    logger.info("vision%s: analysing %s (captured %s)", ctx, segment.name, frame_captured_at.isoformat())

    workdir = Path(tempfile.mkdtemp(prefix="vision_"))
    try:
        frames = extract_frames(segment, FRAME_COUNT, workdir)

        instruction = _INSTRUCTION
        for attempt in range(1, MAX_ATTEMPTS + 1):
            raw = _call_gemini(frames, instruction)
            try:
                parsed = _VisionResponse.model_validate_json(raw)
            except (ValidationError, ValueError) as exc:
                logger.warning("vision%s attempt %d: invalid response: %s", ctx, attempt, exc)
                instruction = _INSTRUCTION + _RETRY_SUFFIX.format(error=str(exc)[:300])
                continue

            logger.info(
                "vision%s attempt %d: %s (confidence %.2f)",
                ctx, attempt, parsed.symptom.value, parsed.confidence,
            )
            return VisionFinding(
                frame_captured_at=frame_captured_at,
                symptom=parsed.symptom,
                description=parsed.description,
                confidence=parsed.confidence,
                model=VISION_MODEL,
                raw_response=raw,
            )

        logger.error(
            "vision%s: %d invalid responses -- returning None (diagnostic should stop)",
            ctx, MAX_ATTEMPTS,
        )
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Manual run
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    finding = analyze_frame()
    if finding is None:
        print("\nNone -- diagnostic should stop (Gemini output failed validation twice)")
    else:
        print()
        print(finding.model_dump_json(indent=2))
