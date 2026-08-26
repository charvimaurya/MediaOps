import os
import signal
import subprocess
import threading
import time

import psutil
from prometheus_client import start_http_server

from simulator.failures import (
    healthy_state,
    compute_telemetry_fields,
)
from simulator import backup as backup_module

from simulator.telemetry import update_metrics


# ============================================================
# CONFIGURATION
# ============================================================

VIDEO_PATH = os.path.join(
    os.path.dirname(__file__),
    "video.mov",
)

# Real segmented output, for simulator/output_accounting.py's RPO
# measurement to have genuine files to count (see that module's
# docstring). Downscaled + bitrate-capped regardless of the source
# video's own resolution -- this exists purely for accounting, not
# playback quality.
OUTPUT_DIR = os.path.join(os.path.dirname(__file__), "output", "primary")
SEGMENT_DURATION_SECONDS = 2

STREAM_NAME = "main_stream"
ENCODER_NAME = "encoder_01"

PROMETHEUS_PORT = 8000

TELEMETRY_INTERVAL = 5


# ============================================================
# GLOBAL PIPELINE STATE
# ============================================================

current_state = healthy_state()

state_lock = threading.Lock()

ffmpeg_process = None


# ============================================================
# FFmpeg
# ============================================================

def start_ffmpeg():

    global ffmpeg_process

    if not os.path.exists(VIDEO_PATH):
        raise FileNotFoundError(
            f"Video not found: {VIDEO_PATH}"
        )

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    gop = 24 * SEGMENT_DURATION_SECONDS  # source is 24fps

    command = [
        "ffmpeg",

        # Suppress unnecessary output
        "-hide_banner",
        "-loglevel",
        "error",

        # Loop video continuously
        "-stream_loop",
        "-1",

        # Read at native speed
        "-re",

        "-i",
        VIDEO_PATH,

        # Real (but cheap) encode -- this is the actual media-processing
        # workload, and also what simulator/output_accounting.py counts
        # real segment files from.
        "-vf", "scale=320:-2",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-b:v", "300k",
        "-g", str(gop),
        "-keyint_min", str(gop),
        "-sc_threshold", "0",
        "-an",

        "-f", "hls",
        "-hls_time", str(SEGMENT_DURATION_SECONDS),
        "-hls_list_size", "0",
        "-hls_segment_filename", os.path.join(OUTPUT_DIR, "segment_%05d.ts"),
        os.path.join(OUTPUT_DIR, "playlist.m3u8"),
    ]

    print("\nStarting FFmpeg media pipeline...")

    ffmpeg_process = subprocess.Popen(
        command,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )

    print(
        f"FFmpeg started with PID "
        f"{ffmpeg_process.pid}"
    )


def restart_ffmpeg() -> bool:
    """Stop the current ffmpeg process (if any) and start a fresh one.
    Returns True once the new process is confirmed running. Used by
    simulator/control.py's restart_encoder() -- kept as a small addition
    here rather than duplicating process-management logic there."""

    global ffmpeg_process

    old = ffmpeg_process

    if old is not None:
        try:
            old.terminate()
            old.wait(timeout=5)
        except subprocess.TimeoutExpired:
            old.kill()

    start_ffmpeg()
    time.sleep(0.2)

    return ffmpeg_process is not None and ffmpeg_process.poll() is None


# ============================================================
# REAL SYSTEM METRICS
# ============================================================

def get_real_cpu_usage():

    if ffmpeg_process is None:
        return 0.0

    try:

        process = psutil.Process(
            ffmpeg_process.pid
        )

        # CPU percentage since previous call
        return process.cpu_percent(
            interval=0.5
        )

    except (
        psutil.NoSuchProcess,
        psutil.AccessDenied,
    ):
        return 0.0


def get_real_memory_usage():

    if ffmpeg_process is None:
        return 0.0

    try:

        process = psutil.Process(
            ffmpeg_process.pid
        )

        memory = process.memory_percent()

        return memory

    except (
        psutil.NoSuchProcess,
        psutil.AccessDenied,
    ):
        return 0.0


# ============================================================
# TELEMETRY LOOP
# ============================================================

def telemetry_loop():

    global current_state

    while True:

        with state_lock:

            state = current_state

            # ------------------------------------------------
            # Publish whichever encoder instance is currently active.
            # compute_telemetry_fields() is the single source of truth
            # for "what does this fault combination look like" -- see
            # simulator/failures.py. Real psutil CPU/memory only apply
            # when the active instance's own encoder layer is healthy
            # (a network fault or an overloaded/dead encoder shouldn't
            # borrow psutil's number for a different condition).
            # ------------------------------------------------

            if state.active_output == "backup":
                backup_instance = backup_module.get_backup()
                fields = compute_telemetry_fields(
                    encoder_fault=backup_instance.state.encoder_fault,
                    network_fault=state.network_fault,
                    bitrate_factor=state.bitrate_factor,
                    real_cpu=backup_instance.baseline_cpu,
                    real_memory=backup_instance.baseline_memory,
                )
            else:
                if state.encoder_fault == "healthy":
                    real_cpu = get_real_cpu_usage()
                    real_memory = get_real_memory_usage()
                else:
                    real_cpu = None
                    real_memory = None
                fields = compute_telemetry_fields(
                    encoder_fault=state.encoder_fault,
                    network_fault=state.network_fault,
                    bitrate_factor=state.bitrate_factor,
                    real_cpu=real_cpu,
                    real_memory=real_memory,
                )

            for key, value in fields.items():
                setattr(state, key, value)

            # ------------------------------------------------
            # Update Prometheus
            # ------------------------------------------------

            update_metrics(
                STREAM_NAME,
                ENCODER_NAME,
                state,
            )

        time.sleep(
            TELEMETRY_INTERVAL
        )


# ============================================================
# CLEAN SHUTDOWN
# ============================================================

def shutdown():

    global ffmpeg_process

    print("\nShutting down MediaOps...")

    if ffmpeg_process:

        try:

            ffmpeg_process.terminate()

            ffmpeg_process.wait(
                timeout=5
            )

        except subprocess.TimeoutExpired:

            ffmpeg_process.kill()

    print("Media pipeline stopped.")


# ============================================================
# MAIN
# ============================================================

def main():

    print(
        """
========================================
       MEDIAOPS AI SIMULATOR
========================================
"""
    )

    print(
        f"Video: {VIDEO_PATH}"
    )

    print(
        f"Prometheus metrics:"
        f" http://localhost:{PROMETHEUS_PORT}/metrics"
    )

    print()

    # Start Prometheus HTTP server
    start_http_server(
        PROMETHEUS_PORT
    )

    # Start FFmpeg
    start_ffmpeg()

    # Handle Ctrl+C
    signal.signal(
        signal.SIGINT,
        lambda sig, frame: shutdown()
    )

    signal.signal(
        signal.SIGTERM,
        lambda sig, frame: shutdown()
    )

    # Start telemetry
    telemetry_thread = threading.Thread(
        target=telemetry_loop,
        daemon=True,
    )

    telemetry_thread.start()

    print(
        "Telemetry collection started."
    )

    print(
        "Media pipeline is HEALTHY."
    )

    print(
        "\nPress CTRL+C to stop.\n"
    )

    # Keep application alive
    while True:

        if (
            ffmpeg_process
            and ffmpeg_process.poll()
            is not None
        ):

            print(
                "WARNING: FFmpeg process stopped."
            )

            break

        time.sleep(1)

    shutdown()


if __name__ == "__main__":
    main()