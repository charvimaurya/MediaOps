"""
The Vision Agent -- real Gemini (via Vertex AI, using Google ADK).

Given a fault type, it extracts 3 stills from the MIDDLE of that fault's section
in the messy video (`simulator/output/messy_video.mov`), sends them to Gemini,
and returns a schema-valid `VisionFinding` describing the viewer-visible symptom.

Sampling the middle of the section (not the boundary) avoids catching a
transition frame. The fault -> section-timestamp map is FAULT_SECTIONS below --
the one place to adjust it.

Tier 1 guardrail: the structured-output check is real. Gemini's answer is parsed
strictly with Pydantic; if it doesn't validate, ONE strict retry; if it still
fails, we return None -- never a malformed or free-form finding.

Contract of `analyze_frame()`:
  - returns a valid VisionFinding on success;
  - returns None if Gemini's output fails validation twice
    -> the caller must STOP the diagnostic;
  - raises on infrastructure failure (messy video missing, ffmpeg error, Gemini
    API/auth/quota error) -- also fail-closed at the caller.

Standalone: `python3 -m agents.vision_agent [fault]` (fault defaults to "overload")
Prereqs: simulator/output/messy_video.mov exists (run simulator/generate_messy_video.py),
         ffmpeg on PATH, ADC configured, Vertex AI enabled.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
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
from observability import log_event

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
MESSY_VIDEO = os.environ.get(
    "MESSY_VIDEO",
    os.path.join(
        os.path.dirname(os.path.dirname(__file__)),
        "simulator",
        "output",
        "messy_video.mov",
    ),
)
FFMPEG_BIN = os.environ.get("FFMPEG_BIN", "ffmpeg")

# fault name -> (start, end) seconds in the messy video. THE one place to adjust.
# Mirrors FAULT_RANGES in simulator/generate_messy_video.py.
FAULT_SECTIONS: dict[str, tuple[float, float]] = {
    "healthy": (0.0, 5.0),
    "overload": (5.0, 11.0),
    "rgb_shift": (11.0, 17.0),
    "encoder_failure": (17.0, 23.0),
}
FRAME_SPREAD_SECONDS = 1.0  # 3 stills at mid-1s / mid / mid+1s (still ~1s apart)

FRAME_COUNT = 3
MAX_ATTEMPTS = 2  # one call + one strict retry
_APP_NAME = "mediaops_vision_agent"


# --------------------------------------------------------------------------- #
# 1. Pull frames from the middle of a fault's section (read-only)
# --------------------------------------------------------------------------- #

def _mid_timestamp(fault: str) -> float:
    """Midpoint (seconds) of `fault`'s section. Raises on an unknown fault."""
    if fault not in FAULT_SECTIONS:
        raise ValueError(
            f"unknown fault {fault!r}; expected one of {sorted(FAULT_SECTIONS)}"
        )
    start, end = FAULT_SECTIONS[fault]
    return (start + end) / 2.0


def extract_section_frames(video: Path, fault: str, workdir: Path) -> list[Path]:
    """
    Three JPEG stills clustered at the middle of `fault`'s section -- far from the
    section boundaries so we never catch a transition frame. Written into `workdir`.
    """
    if not video.exists():
        raise FileNotFoundError(
            f"messy video not found: {video} -- run simulator/generate_messy_video.py"
        )
    mid = _mid_timestamp(fault)
    frames: list[Path] = []
    for i, off in enumerate((-FRAME_SPREAD_SECONDS, 0.0, FRAME_SPREAD_SECONDS)):
        out = workdir / f"frame_{i:02d}.jpg"
        subprocess.run(
            [FFMPEG_BIN, "-hide_banner", "-loglevel", "error",
             "-ss", f"{mid + off:.3f}", "-i", str(video),
             "-frames:v", "1", "-q:v", "3", "-y", str(out)],
            check=True, timeout=20,
        )
        if not out.exists():
            raise RuntimeError(f"ffmpeg produced no frame at t={mid + off:.2f}s from {video}")
        frames.append(out)
    return frames


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
- RGB_SHIFT: persistent colour-channel misregistration / chromatic aberration on every edge, present throughout the frame -- a broadcast fault, not intentional style
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
  "symptom": one of NORMAL, BLACK_FRAME, FROZEN_FRAME, MACROBLOCKING, RGB_SHIFT, COLOR_BARS, SLATE, OTHER
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
    fault: str = "healthy",
    video_path: Optional[str] = None,
) -> Optional[VisionFinding]:
    """See module docstring for the contract. `fault` selects which section of the
    messy video to sample: one of FAULT_SECTIONS' keys."""
    video = Path(video_path or MESSY_VIDEO)
    ctx = f" (incident {incident.incident_id})" if incident is not None else ""
    mid = _mid_timestamp(fault)  # validates `fault` before any work
    frame_captured_at = datetime.now(timezone.utc)
    logger.info("vision%s: fault=%s -> %s around t=%.1fs", ctx, fault, video.name, mid)

    workdir = Path(tempfile.mkdtemp(prefix="vision_"))
    try:
        frames = extract_section_frames(video, fault, workdir)

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
            # Preserve Gemini's exact response with the structured finding for
            # later diagnosis; the event log below is only its searchable summary.
            finding = VisionFinding(
                frame_captured_at=frame_captured_at,
                symptom=parsed.symptom,
                description=parsed.description,
                confidence=parsed.confidence,
                model=VISION_MODEL,
                raw_response=raw,
            )
            if incident is not None:
                log_event("vision", incident.incident_id, "diagnose", "completed",
                          detail=f"{finding.symptom.value}; confidence={finding.confidence:.3f}")
            return finding

        logger.error(
            "vision%s: %d invalid responses -- returning None (diagnostic should stop)",
            ctx, MAX_ATTEMPTS,
        )
        if incident is not None:
            log_event("vision", incident.incident_id, "diagnose", "no_valid_finding",
                      detail=f"{MAX_ATTEMPTS} invalid responses")
        return None
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Manual run
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    args = sys.argv[1:]
    from tools.incident_cli import looks_like_incident_id, run_step
    from models import IncidentStatus

    if args and looks_like_incident_id(args[0]):
        # incident mode: read the doc, sample the messy video, write inc.vision
        section = args[1] if len(args) > 1 else "overload"
        run_step(
            args[0], step_name="diagnose", status=IncidentStatus.DIAGNOSING,
            produces="vision",
            compute=lambda inc: analyze_frame(inc, fault=section),
        )
    else:
        fault_arg = args[0] if args else "overload"
        finding = analyze_frame(fault=fault_arg)
        if finding is None:
            print("\nNone -- diagnostic should stop (Gemini output failed validation twice)")
        else:
            print()
            print(finding.model_dump_json(indent=2))
