"""
generate_messy_video.py -- offline messy-video generator.

Standalone utility. Reads the clean source video and writes ONE 23-second video
with three REAL, pixel-level visual faults baked into three time windows:

    0 - 5 s   healthy    -- untouched clean baseline
    5 - 11 s  overload   -- quantizer crushed to QP 46, deblocking off
                             -> huge sharp DCT block artifacts
    11 - 17 s rgb shift  -- red plane -7px, blue plane +7px, green fixed
                             -> visible colour misregistration
    17 - 23 s failure    -- every pixel forced to black

The damage is real: sections are re-encoded / filtered and then decoded back to
raw pixels during the final concat, so the artifacts are genuine content in the
output -- nothing faked or hardcoded.

Deterministic: same source in -> byte-identical frames out, every run
(-threads 1 + x264 bitexact + pure filters + stripped metadata).

This file NEVER imports or modifies any other simulator module. It only reads
the source video and writes a new file.

    python3 simulator/generate_messy_video.py [SOURCE] [OUTPUT]

Defaults: SOURCE = simulator/video.mov (or video.mp4),  OUTPUT = simulator/video_messy.mov
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_SOURCES = [HERE / "video.mov", HERE / "video.mp4"]
DEFAULT_OUTPUT = HERE / "video_messy.mov"

TOTAL_SECONDS = 23
SECTIONS = [
    (0, 5, "healthy -- clean baseline (untouched)"),
    (5, 11, "encoder overload -- QP 46 crush, deblocking off (sharp DCT blocks)"),
    (11, 17, "RGB channel shift -- red -7px / blue +7px vs green"),
    (17, 23, "encoder failure -- forced black"),
]

FFMPEG = "ffmpeg"
FFPROBE = "ffprobe"

# every ffmpeg invocation gets these
_BASE = ["-nostdin", "-y", "-hide_banner", "-loglevel", "error"]
_BITEXACT = ["-fflags", "+bitexact", "-flags:v", "+bitexact"]
_ENC_COMMON = ["-an", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-threads", "1"]


def _run(args: list[str]) -> None:
    subprocess.run([FFMPEG, *_BASE, *args], check=True)


def probe_fps(src: Path) -> int:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    num, _, den = out.partition("/")
    return round(int(num) / int(den or 1))


def probe_duration(src: Path) -> float:
    out = subprocess.run(
        [FFPROBE, "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(src)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def cut(src: Path, start: int, end: int, dst: Path) -> Path:
    """Frame-accurate lossless cut of [start, end)."""
    _run([
        "-i", str(src), "-ss", str(start), "-to", str(end),
        *_ENC_COMMON, "-c:v", "libx264", "-qp", "0",
        "-x264-params", "bitexact=1", *_BITEXACT,
        str(dst),
    ])
    return dst


def damage_overload(src: Path, dst: Path) -> Path:
    """Crush the quantizer and turn off deblocking -> real, sharp DCT blocking."""
    _run([
        "-i", str(src),
        *_ENC_COMMON, "-c:v", "libx264", "-qp", "46", "-bf", "0",
        "-x264-params", "aq-mode=0:deblock=-6,-6:psy-rd=0:no-mbtree=1:bitexact=1",
        *_BITEXACT,
        str(dst),
    ])
    return dst


def damage_rgb_shift(src: Path, dst: Path) -> Path:
    """Shift red and blue planes away from green -> real colour misregistration."""
    _run([
        "-i", str(src),
        "-vf", "rgbashift=rh=-7:rv=0:bh=7:bv=0:edge=smear",
        *_ENC_COMMON, "-c:v", "libx264", "-qp", "0",
        "-x264-params", "bitexact=1", *_BITEXACT,
        str(dst),
    ])
    return dst


def damage_black(src: Path, dst: Path) -> Path:
    """Force every pixel to black -> real black frames."""
    _run([
        "-i", str(src),
        "-vf", "drawbox=x=0:y=0:w=iw:h=ih:color=black@1.0:t=fill",
        *_ENC_COMMON, "-c:v", "libx264", "-qp", "0",
        "-x264-params", "bitexact=1", *_BITEXACT,
        str(dst),
    ])
    return dst


def concat(pieces: list[Path], out: Path, fps: int) -> None:
    norm = "".join(
        f"[{i}:v]fps={fps},format=yuv420p,setsar=1[v{i}];" for i in range(len(pieces))
    )
    joins = "".join(f"[v{i}]" for i in range(len(pieces)))
    graph = f"{norm}{joins}concat=n={len(pieces)}:v=1:a=0[v]"
    args: list[str] = []
    for p in pieces:
        args += ["-i", str(p)]
    args += [
        "-filter_complex", graph, "-map", "[v]", "-t", str(TOTAL_SECONDS),
        *_ENC_COMMON, "-c:v", "libx264", "-crf", "18",
        "-r", str(fps), "-g", str(fps * 2),
        "-x264-params", "bitexact=1", *_BITEXACT,
        "-map_metadata", "-1", "-movflags", "+faststart",
        str(out),
    ]
    _run(args)


def frame_digest(path: Path) -> str:
    """sha256 of the per-frame MD5 list -- a pixel-exact, container-independent digest."""
    proc = subprocess.run(
        [FFMPEG, *_BASE, "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"],
        capture_output=True, text=True, check=True,
    )
    body = "\n".join(l for l in proc.stdout.splitlines() if l and not l.startswith("#"))
    return hashlib.sha256(body.encode()).hexdigest()


def main() -> None:
    if len(sys.argv) > 1:
        src = Path(sys.argv[1])
    else:
        src = next((p for p in DEFAULT_SOURCES if p.exists()), None)
    if src is None or not src.exists():
        sys.exit(f"source video not found (looked for {', '.join(map(str, DEFAULT_SOURCES))})")
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTPUT

    fps = probe_fps(src)
    dur = probe_duration(src)
    if dur < TOTAL_SECONDS - 0.05:
        sys.exit(f"source is only {dur:.2f}s -- need at least {TOTAL_SECONDS}s")

    print(f"source : {src}  ({fps} fps, {dur:.2f}s)")
    print(f"output : {out}")
    print("sections:")
    for a, b, label in SECTIONS:
        print(f"  {a:>2}-{b:<2}s  {label}")

    tmp = Path(tempfile.mkdtemp(prefix="messy_"))
    try:
        (a0, b0, _), (a1, b1, _), (a2, b2, _), (a3, b3, _) = SECTIONS
        p0 = cut(src, a0, b0, tmp / "p0.mp4")
        p1 = damage_overload(cut(src, a1, b1, tmp / "c1.mp4"), tmp / "p1.mp4")
        p2 = damage_rgb_shift(cut(src, a2, b2, tmp / "c2.mp4"), tmp / "p2.mp4")
        p3 = damage_black(cut(src, a3, b3, tmp / "c3.mp4"), tmp / "p3.mp4")
        concat([p0, p1, p2, p3], out, fps)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    outdur = probe_duration(out)
    digest = frame_digest(out)
    print(f"\ndone: {out}  ({outdur:.3f}s)")
    print(f"frame digest (sha256 of framemd5): {digest}")
    print(f"file sha256: {hashlib.sha256(out.read_bytes()).hexdigest()}")
    print("\nto use it in the simulator (your call -- not done automatically):")
    print(f"  cp {out} {HERE / 'video.mov'}")
    print("  # then restart:  uvicorn simulator.control_api:app --port 8001")


if __name__ == "__main__":
    main()
