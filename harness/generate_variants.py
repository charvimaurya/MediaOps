#!/usr/bin/env python3
"""Generate broken-video test variants from a clean reference clip.

Reads a single reference clip from harness/reference/ and writes three
degraded variants into harness/variants/, plus a manifest.json recording
ground truth (artifact_class, degradation start time, expected root cause)
for each one. Re-running regenerates everything from scratch, so it is
safe to re-run any time the reference clip or this script changes.

Usage:
    python3 harness/generate_variants.py
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path

HARNESS_DIR = Path(__file__).resolve().parent
REFERENCE_DIR = HARNESS_DIR / "reference"
VARIANTS_DIR = HARNESS_DIR / "variants"
TMP_DIR = VARIANTS_DIR / ".tmp"
MANIFEST_PATH = VARIANTS_DIR / "manifest.json"

REFERENCE_VIDEO_EXTENSIONS = (".mp4", ".mov", ".mkv", ".avi", ".m4v")

CLEAN_SECONDS = 20.0          # picture must be clean for at least this long
MIN_DURATION_SECONDS = CLEAN_SECONDS + 5.0
EXPECTED_DURATION_RANGE = (60.0, 90.0)  # soft check only, per harness assumptions

MACROBLOCK_BITRATE = "50k"    # crushed enough to produce obvious blocking

FREEZE_PLAY_SECONDS = 8.0     # normal playback between freezes
FREEZE_HOLD_SECONDS = 3.0     # how long each freeze holds

AUDIO_DELAY_MS = 400


def fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


def check_tool(name: str) -> None:
    if shutil.which(name) is None:
        fail(f"'{name}' not found on PATH. Install ffmpeg (e.g. `brew install ffmpeg`).")


def find_reference() -> Path:
    if not REFERENCE_DIR.exists():
        fail(f"{REFERENCE_DIR} does not exist.")
    candidates = sorted(
        p for p in REFERENCE_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in REFERENCE_VIDEO_EXTENSIONS
    )
    if not candidates:
        fail(
            f"no reference clip found in {REFERENCE_DIR}. "
            f"Add one file (e.g. reference.mp4)."
        )
    if len(candidates) > 1:
        names = ", ".join(p.name for p in candidates)
        fail(
            f"expected exactly one reference clip in {REFERENCE_DIR}, "
            f"found {len(candidates)}: {names}"
        )
    return candidates[0]


def ffprobe_json(path: Path, *args: str) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-of", "json", *args, str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        fail(f"ffprobe failed on {path}:\n{result.stderr}")
    return json.loads(result.stdout)


def probe_reference(path: Path) -> dict:
    fmt = ffprobe_json(path, "-show_entries", "format=duration")
    duration = float(fmt["format"]["duration"])

    vstream = ffprobe_json(
        path, "-select_streams", "v:0",
        "-show_entries", "stream=r_frame_rate,width,height",
    )["streams"]
    if not vstream:
        fail(f"{path} has no video stream.")
    v = vstream[0]
    fps = Fraction(v["r_frame_rate"])

    astream = ffprobe_json(
        path, "-select_streams", "a:0",
        "-show_entries", "stream=sample_rate,channels",
    )["streams"]
    if not astream:
        fail(f"{path} has no audio stream.")
    a = astream[0]

    return {
        "duration": duration,
        "fps": fps,
        "width": int(v["width"]),
        "height": int(v["height"]),
        "sample_rate": a["sample_rate"],
        "channels": int(a["channels"]),
    }


def run_ffmpeg(args: list) -> None:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *args]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        fail(f"ffmpeg command failed:\n{' '.join(cmd)}\n{result.stderr}")


def fps_str(fps: Fraction) -> str:
    return f"{fps.numerator}/{fps.denominator}"


def encode_av_chunk(ref: Path, info: dict, start: float, duration: float, out_path: Path) -> None:
    """Trim [start, start+duration) from ref, re-encoded to shared params."""
    run_ffmpeg([
        "-ss", f"{start}", "-t", f"{duration}", "-i", str(ref),
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", fps_str(info["fps"]),
        "-c:a", "aac", "-ar", str(info["sample_rate"]), "-ac", str(info["channels"]),
        str(out_path),
    ])


def encode_freeze_chunk(ref: Path, info: dict, freeze_at: float, hold_seconds: float, out_path: Path) -> None:
    """Hold the frame at `freeze_at` for hold_seconds, audio continues normally."""
    frame_path = out_path.with_suffix(".png")
    run_ffmpeg([
        "-ss", f"{freeze_at}", "-i", str(ref),
        "-vframes", "1", str(frame_path),
    ])
    run_ffmpeg([
        "-loop", "1", "-framerate", fps_str(info["fps"]), "-i", str(frame_path),
        "-ss", f"{freeze_at}", "-t", f"{hold_seconds}", "-i", str(ref),
        "-map", "0:v:0", "-map", "1:a:0", "-t", f"{hold_seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", fps_str(info["fps"]),
        "-c:a", "aac", "-ar", str(info["sample_rate"]), "-ac", str(info["channels"]),
        str(out_path),
    ])
    frame_path.unlink(missing_ok=True)


def encode_crushed_chunk(ref: Path, info: dict, start: float, duration: float, out_path: Path) -> None:
    run_ffmpeg([
        "-ss", f"{start}", "-t", f"{duration}", "-i", str(ref),
        "-map", "0:v:0", "-map", "0:a:0",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", fps_str(info["fps"]),
        "-b:v", MACROBLOCK_BITRATE, "-maxrate", MACROBLOCK_BITRATE, "-bufsize", "20k",
        "-c:a", "aac", "-ar", str(info["sample_rate"]), "-ac", str(info["channels"]),
        str(out_path),
    ])


def concat_chunks(chunks: list, out_path: Path) -> None:
    list_path = out_path.with_suffix(".txt")
    with open(list_path, "w") as f:
        for chunk in chunks:
            f.write(f"file '{chunk.resolve()}'\n")
    run_ffmpeg([
        "-f", "concat", "-safe", "0", "-i", str(list_path),
        "-c", "copy", str(out_path),
    ])
    list_path.unlink()


def build_macroblocking(ref: Path, info: dict, clean_chunk: Path) -> Path:
    out_path = VARIANTS_DIR / "macroblocking.mp4"
    crushed = TMP_DIR / "macroblocking_crushed.ts"
    encode_crushed_chunk(ref, info, CLEAN_SECONDS, info["duration"] - CLEAN_SECONDS, crushed)
    concat_chunks([clean_chunk, crushed], out_path)
    return out_path


def build_freeze(ref: Path, info: dict, clean_chunk: Path) -> tuple:
    out_path = VARIANTS_DIR / "freeze.mp4"
    chunks = [clean_chunk]
    freeze_events = []

    t = CLEAN_SECONDS
    end = info["duration"]
    i = 0
    while end - t >= FREEZE_PLAY_SECONDS + FREEZE_HOLD_SECONDS:
        play_chunk = TMP_DIR / f"freeze_play_{i}.ts"
        encode_av_chunk(ref, info, t, FREEZE_PLAY_SECONDS, play_chunk)
        chunks.append(play_chunk)
        t += FREEZE_PLAY_SECONDS

        freeze_chunk = TMP_DIR / f"freeze_hold_{i}.ts"
        encode_freeze_chunk(ref, info, t, FREEZE_HOLD_SECONDS, freeze_chunk)
        chunks.append(freeze_chunk)
        freeze_events.append({"starts_at_seconds": round(t, 2), "duration_seconds": FREEZE_HOLD_SECONDS})
        t += FREEZE_HOLD_SECONDS
        i += 1

    if end - t > 0:
        remainder_chunk = TMP_DIR / "freeze_play_final.ts"
        encode_av_chunk(ref, info, t, end - t, remainder_chunk)
        chunks.append(remainder_chunk)

    if not freeze_events:
        fail("reference clip too short to fit even one freeze cycle after the clean window.")

    concat_chunks(chunks, out_path)
    return out_path, freeze_events


def build_audio_drift(ref: Path, info: dict) -> Path:
    out_path = VARIANTS_DIR / "audio_drift.mp4"
    delay_ms = "|".join([str(AUDIO_DELAY_MS)] * info["channels"])
    filter_complex = (
        f"[0:a]atrim=0:{CLEAN_SECONDS},asetpts=PTS-STARTPTS[a1];"
        f"[0:a]atrim=start={CLEAN_SECONDS},asetpts=PTS-STARTPTS,adelay={delay_ms}[a2];"
        f"[a1][a2]concat=n=2:v=0:a=1[aout]"
    )
    run_ffmpeg([
        "-i", str(ref),
        "-filter_complex", filter_complex,
        "-map", "0:v:0", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-ar", str(info["sample_rate"]),
        "-shortest",
        str(out_path),
    ])
    return out_path


def main() -> None:
    check_tool("ffmpeg")
    check_tool("ffprobe")

    ref = find_reference()
    info = probe_reference(ref)

    if info["duration"] < MIN_DURATION_SECONDS:
        fail(
            f"{ref.name} is only {info['duration']:.1f}s long; need at least "
            f"{MIN_DURATION_SECONDS:.0f}s ({CLEAN_SECONDS:.0f}s clean + room to degrade)."
        )
    low, high = EXPECTED_DURATION_RANGE
    if not (low <= info["duration"] <= high):
        print(
            f"warning: {ref.name} is {info['duration']:.1f}s; harness assumes "
            f"{low:.0f}-{high:.0f}s. Continuing anyway.",
            file=sys.stderr,
        )

    if TMP_DIR.exists():
        shutil.rmtree(TMP_DIR)
    TMP_DIR.mkdir(parents=True)
    VARIANTS_DIR.mkdir(exist_ok=True)
    for stale in ("macroblocking.mp4", "freeze.mp4", "audio_drift.mp4"):
        (VARIANTS_DIR / stale).unlink(missing_ok=True)

    try:
        clean_chunk = TMP_DIR / "clean.ts"
        encode_av_chunk(ref, info, 0.0, CLEAN_SECONDS, clean_chunk)

        macroblocking_path = build_macroblocking(ref, info, clean_chunk)
        freeze_path, freeze_events = build_freeze(ref, info, clean_chunk)
        audio_drift_path = build_audio_drift(ref, info)
    finally:
        shutil.rmtree(TMP_DIR, ignore_errors=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "reference": {
            "path": str(ref.relative_to(HARNESS_DIR.parent)),
            "duration_seconds": round(info["duration"], 2),
        },
        "variants": [
            {
                "variant_id": "macroblocking",
                "file": str(macroblocking_path.relative_to(HARNESS_DIR.parent)),
                "artifact_class": "macroblocking",
                "degradation_starts_at_seconds": CLEAN_SECONDS,
                "expected_root_cause": "encoder bitrate crushed (insufficient encoder capacity)",
                "description": (
                    f"Clean for the first {CLEAN_SECONDS:.0f}s, then encoded at "
                    f"{MACROBLOCK_BITRATE} for the remainder to force visible blocking."
                ),
            },
            {
                "variant_id": "freeze",
                "file": str(freeze_path.relative_to(HARNESS_DIR.parent)),
                "artifact_class": "freeze",
                "degradation_starts_at_seconds": CLEAN_SECONDS,
                "expected_root_cause": "origin stalled, repeatedly serving a duplicated/stuck frame",
                "description": (
                    f"Clean for the first {CLEAN_SECONDS:.0f}s, then repeated freezes: "
                    f"{FREEZE_PLAY_SECONDS:.0f}s of normal playback followed by a "
                    f"{FREEZE_HOLD_SECONDS:.0f}s frozen frame, audio continues under the freeze."
                ),
                "details": {"freeze_events": freeze_events},
            },
            {
                "variant_id": "audio_drift",
                "file": str(audio_drift_path.relative_to(HARNESS_DIR.parent)),
                "artifact_class": "audio_drift",
                "degradation_starts_at_seconds": CLEAN_SECONDS,
                "expected_root_cause": "packager introduced a constant 400ms audio delay (A/V sync drift)",
                "description": (
                    f"In sync for the first {CLEAN_SECONDS:.0f}s, then audio is delayed by "
                    f"{AUDIO_DELAY_MS}ms relative to video for the remainder."
                ),
            },
        ],
    }
    MANIFEST_PATH.write_text(json.dumps(manifest, indent=2) + "\n")

    print(f"wrote {macroblocking_path}")
    print(f"wrote {freeze_path}")
    print(f"wrote {audio_drift_path}")
    print(f"wrote {MANIFEST_PATH}")


if __name__ == "__main__":
    main()
